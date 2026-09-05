import frappe
from frappe import _
from frappe.utils import flt, strip_html_tags
import json
import datetime
from erpnext.accounts.doctype.bank_transaction.bank_transaction import (
    get_related_bank_gl_entries,
    get_total_allocated_amount,
)
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import (
    get_linked_payments as _get_linked_payments,
)
from erpnext.accounts.party import get_party_account
from erpnext import get_default_cost_center

@frappe.whitelist()
def clear_clearing_date(voucher_type: str, voucher_name: str):
    """
        Clear the clearing date of a voucher
    """
    # using db_set to trigger notification
    payment_entry = frappe.get_doc(voucher_type, voucher_name)

    if payment_entry.has_permission("write"):
        payment_entry.db_set("clearance_date", None)


@frappe.whitelist()
def reconcile_vouchers(bank_transaction_name: str | int, vouchers: str, is_new_voucher: bool = False):
	 
    # updated clear date of all the vouchers based on the bank transaction
    vouchers = json.loads(vouchers)
    transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
    
    # Add the vouchers with zero allocation. Save() will perform the allocations and clearance
    # We are overriding the default behavior of the method to set the reconciliation type
    if 0.0 >= transaction.unallocated_amount:
        frappe.throw(_("Bank Transaction {0} is already fully reconciled").format(transaction.name))
    
    for voucher in vouchers:
        transaction.append(
            "payment_entries",
            {
                "payment_document": voucher["payment_doctype"],
                "payment_entry": voucher["payment_name"],
                "allocated_amount": 0.0,  # Temporary
                "reconciliation_type": "Voucher Created" if is_new_voucher else "Matched",
            },
        )
    transaction.validate_duplicate_references()
    transaction.allocate_payment_entries()
    transaction.update_allocated_amount()
    transaction.set_status()
    transaction.save()

    payment_entries = [
        (voucher["payment_doctype"], voucher["payment_name"])
        for voucher in vouchers
        if voucher["payment_doctype"] == "Payment Entry"
    ]
    if not payment_entries:
        return transaction

    allocated_amounts = get_total_allocated_amount(payment_entries)
    bank_gl_entries = get_related_bank_gl_entries(payment_entries)
    bank_gl_account = frappe.db.get_value("Bank Account", transaction.bank_account, "account")
    account_field = "paid_to" if transaction.deposit > 0 else "paid_from"
    amount_field = "received_amount_after_tax" if transaction.deposit > 0 else "paid_amount_after_tax"
    precision = transaction.precision("unallocated_amount")
    payments = frappe.get_list(
        "Payment Entry",
        filters={"name": ("in", [payment_entry[1] for payment_entry in payment_entries])},
        fields=["name", "payment_type", account_field, amount_field, "clearance_date"],
        limit_page_length=0,
    )
    payments = {payment.name: payment for payment in payments}

    for payment_entry in payment_entries:
        payment = payments.get(payment_entry[1])
        payment_allocations = allocated_amounts.get(payment_entry, {})
        allocation = payment_allocations.get(bank_gl_account, {})
        other_bank_accounts_reconciled = all(
            flt(amount, precision)
            == flt(payment_allocations.get(account, {}).get("total"), precision)
            for account, amount in bank_gl_entries.get(payment_entry, {}).items()
            if account != bank_gl_account
        )
        if (
            payment
            and payment.payment_type != "Internal Transfer"
            and not payment.clearance_date
            and payment.get(account_field) == bank_gl_account
            and other_bank_accounts_reconciled
            and flt(allocation.get("total"), precision) > 0
            and flt(allocation.get("total"), precision) == flt(payment.get(amount_field), precision)
        ):
            frappe.db.set_value(
                "Payment Entry",
                payment_entry[1],
                "clearance_date",
                allocation.get("latest_date") or transaction.date,
            )
    
    return transaction

@frappe.whitelist()
def unreconcile_transaction(transaction_name: str | int):
    """
        Unreconcile an entire bank transaction - so this does not handle individual entries

        If the individual entries in the bank transaction are matched, just remove the payment entries
        Else, cancel the individual entries
    """
    transaction = frappe.get_doc("Bank Transaction", transaction_name)

    vouchers_to_cancel = []

    for entry in transaction.payment_entries:
        if entry.reconciliation_type == "Voucher Created":
            vouchers_to_cancel.append({
                "doctype": entry.payment_document,
                "name": entry.payment_entry,
            })
            
    transaction.remove_payment_entries()

    for voucher in vouchers_to_cancel:
        frappe.get_doc(voucher["doctype"], voucher["name"]).cancel()

@frappe.whitelist()
def undo_reconciliation_action(bank_transaction_id: str | int, voucher_type: str, voucher_id: str | int):
    """
     API to remove a single reconciliation action - for example only undoing one voucher instead of undoing the entire transaction
    """

    bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_id)

    # Find the voucher in the bank transaction and depending on the action, either remove it or cancel the voucher
    for entry in bank_transaction.payment_entries:
        if entry.payment_document == voucher_type and entry.payment_entry == voucher_id:
            if entry.reconciliation_type  == "Voucher Created":
                frappe.get_doc(voucher_type, voucher_id).cancel()
            else:
                bank_transaction.remove_payment_entry(entry)
                bank_transaction.save()

    return {
        "success": True,
    }


def run_bulk_item(bank_transaction_name: str | int, action, results: list, errors: list):
    """
        Run one item of a bulk action as its own database transaction.

        Every Payment Entry / Journal Entry insert takes a `FOR UPDATE` lock on the naming
        series row in `tabSeries`, and InnoDB holds it until the transaction commits. Without
        an explicit commit per item, the first insert of a bulk run keeps that lock for the
        whole request and every concurrent insert of the same doctype elsewhere on the site
        waits 50s and dies with "Lock wait timeout exceeded". Committing per item narrows that
        hold to one item - it does not remove it, because the commit only comes after the voucher
        has been inserted, submitted and reconciled.

        Committing after each item also means a failing item no longer discards the whole
        batch: the rollback only goes back to the previous item's commit.
    """
    # `frappe.throw` appends to the message log before raising. Swallowing the exception would
    # otherwise leave those messages to be serialised into `_server_messages` on a 200 response,
    # so the depth is recorded here and anything the failed item added is dropped below.
    # getattr: this function's contract is that it never lets the loop die, so even the
    # bookkeeping before the try avoids assuming the request-local is initialised.
    message_log_depth = len(getattr(frappe.local, "message_log", []))

    try:
        result = action()
    except Exception as e:
        error = str(e) or e.__class__.__name__

        try:
            del frappe.local.message_log[message_log_depth:]
            # Rolls back to the previous item's commit, so items already done survive.
            frappe.db.rollback()
            frappe.log_error(
                title=f"Mint bulk reconciliation failed: {bank_transaction_name}"[:140],
                message=frappe.get_traceback(),
            )
            # The Error Log row lands in the transaction the rollback just re-opened. Without this
            # commit the next failing item's rollback would wipe it, so a run where several items
            # fail in a row would keep only the last traceback.
            frappe.db.commit()
        except Exception:
            # A dead connection makes the recovery itself raise. The loop has to survive that:
            # otherwise the caller gets a bare 500 and cannot tell which items already committed.
            # The database is unusable at this point, so this goes to the file log.
            frappe.logger().error(
                f"Mint bulk reconciliation could not record the failure of {bank_transaction_name}",
                exc_info=True,
            )

        # Appended outside the recovery block so a failed rollback still reports the item.
        errors.append({
            "bank_transaction": str(bank_transaction_name),
            # ERPNext throws can carry markup (frappe.bold, get_link_to_form) and the toast
            # renders this as text, so the tags would otherwise show up literally.
            "error": strip_html_tags(error),
        })
    else:
        # The item is complete (voucher created, submitted and reconciled) - release the locks.
        frappe.db.commit()
        results.append(result)


@frappe.whitelist(methods=["POST"])
def create_bulk_internal_transfer(bank_transaction_names: list[str|int], 
                                  bank_account: str):
    """
        Create an internal transfer for multiple bank transactions
    """
    results = []
    errors = []

    for bank_transaction_name in bank_transaction_names:

        def action(bank_transaction_name=bank_transaction_name):
            bank_transaction = frappe.db.get_value("Bank Transaction", bank_transaction_name, ["name", "withdrawal", "bank_account", "date", "reference_number", "description"], as_dict=True)

            transaction_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")

            is_withdrawal = bank_transaction.withdrawal > 0.0

            if is_withdrawal:
                paid_from = transaction_account
                paid_to = bank_account
            else:
                paid_from = bank_account
                paid_to = transaction_account

            reference_no = (bank_transaction.reference_number or bank_transaction.description or '')[:140]

            return create_internal_transfer(bank_transaction_name=bank_transaction.name,
                                     posting_date=bank_transaction.date,
                                     reference_date=bank_transaction.date,
                                     reference_no=reference_no,
                                     paid_from=paid_from,
                                     paid_to=paid_to,)

        run_bulk_item(bank_transaction_name, action, results, errors)

    return {
        "results": results,
        "errors": errors,
    }

@frappe.whitelist()
def create_internal_transfer(bank_transaction_name: str|int, 
                             posting_date: str | datetime.date, 
                             reference_date: str | datetime.date, 
                             reference_no: str, 
                             paid_from: str, 
                             paid_to: str,
                             custom_remarks: bool = False,
                             remarks: str = None,
                             mirror_transaction_name: str | int = None,
                             dimensions: dict = None):
    """
    Create an internal transfer payment entry
    """

    bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)

    bank_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")
    company = frappe.get_cached_value("Account", bank_account, "company")

    is_withdrawal = bank_transaction.withdrawal > 0.0

    pe = frappe.new_doc("Payment Entry")

    pe.company = company
    pe.payment_type = "Internal Transfer"
    pe.posting_date = posting_date
    pe.reference_date = reference_date
    pe.reference_no = reference_no
    pe.custom_remarks = custom_remarks
    pe.paid_amount = bank_transaction.unallocated_amount
    pe.received_amount = bank_transaction.unallocated_amount

    # TODO: Support multi-currency transactions
    pe.target_exchange_rate = 1.0

    if custom_remarks:
        pe.remarks = remarks
    
    if dimensions:
        pe.update(dimensions)

    if is_withdrawal:
         pe.paid_to = paid_to
         pe.paid_from = bank_account
    else:
         pe.paid_from = paid_from
         pe.paid_to = bank_account
    
    pe.insert()
    pe.submit()

    vouchers = json.dumps(
		[
			{
				"payment_doctype": "Payment Entry",
				"payment_name": pe.name,
				"amount": bank_transaction.unallocated_amount,
			}
		]
	)

    transaction_id = reconcile_vouchers(bank_transaction_name, vouchers, is_new_voucher=True)

    if mirror_transaction_name:
        # Reconcile the mirror transaction
        reconcile_vouchers(mirror_transaction_name, vouchers, is_new_voucher=False)

    return {
        "transaction": transaction_id,
        "payment_entry": pe,
    }

@frappe.whitelist(methods=['POST'])
def create_bulk_bank_entry_and_reconcile(bank_transactions: list[str|int],
                                         account: str,
                                         party_type: str | None = None,
                                         party: str | None = None):
    """
     Create bank entries for all transactions and reconcile them
    """

    results = []
    errors = []

    for bank_transaction in bank_transactions:

        def action(bank_transaction=bank_transaction):
            transactions_details = frappe.db.get_value("Bank Transaction", bank_transaction, ["name", "deposit", "withdrawal", "bank_account", "currency", "unallocated_amount", "date", "reference_number", "description"], as_dict=True)

            is_credit_card = frappe.get_cached_value("Bank Account", transactions_details.bank_account, "is_credit_card")

            # Check Number will be limited to 140 characters
            cheque_no = (transactions_details.reference_number or transactions_details.description or '')[:140]

            is_withdrawal = transactions_details.withdrawal > 0.0

            entries = []

            gl_account = frappe.get_cached_value("Bank Account", transactions_details.bank_account, "account")

            if is_withdrawal:
                entries.append({
                    "account": gl_account,
                    "bank_account": transactions_details.bank_account,
                    "credit_in_account_currency": transactions_details.unallocated_amount,
                    "credit": transactions_details.unallocated_amount,
                    "debit_in_account_currency": 0,
                    "debit": 0,
                })

                entries.append({
                    "account": account,
                    "party_type": party_type if party else None,
                    "party": party,
                    "credit": 0,
                    "debit": transactions_details.unallocated_amount,
                })
            else:
                entries.append({
                    "account": gl_account,
                    "bank_account": transactions_details.bank_account,
                    "debit_in_account_currency": transactions_details.unallocated_amount,
                    "debit": transactions_details.unallocated_amount,
                    "credit_in_account_currency": 0,
                    "credit": 0,
                })

                entries.append({
                    "account": account,
                    "party_type": party_type if party else None,
                    "party": party,
                    "debit": 0,
                    "credit": transactions_details.unallocated_amount,
                })

            return create_bank_entry_and_reconcile(bank_transaction_name=bank_transaction,
                                            cheque_date=transactions_details.date,
                                            posting_date=transactions_details.date,
                                            cheque_no=cheque_no,
                                            user_remark=transactions_details.description,
                                            entries=entries,
                                            voucher_type=("Credit Card Entry" if is_credit_card else "Bank Entry"))

        run_bulk_item(bank_transaction, action, results, errors)

    return {
        "results": results,
        "errors": errors,
    }



@frappe.whitelist(methods=['POST'])
def create_bank_entry_and_reconcile(bank_transaction_name: str | int, 
                                    cheque_date: str | datetime.date,
                                    posting_date: str | datetime.date,
                                    cheque_no: str,
                                    entries: list,
                                    user_remark: str = None,
                                    voucher_type: str = "Bank Entry",
                                    dimensions: dict = None):
    """
        Create a bank entry and reconcile it with the bank transaction
    """
    # Create a new journal entry based on the bank transaction
    bank_transaction = frappe.db.get_values(
        "Bank Transaction",
        bank_transaction_name,
        fieldname=["name", "deposit", "withdrawal", "bank_account", "currency", "unallocated_amount"],
        as_dict=True,
    )[0]

    bank_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")
    company = frappe.get_cached_value("Account", bank_account, "company")

    default_cost_center = get_default_cost_center(company)

    bank_entry = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": voucher_type,
        "company": company,
        "cheque_date": cheque_date,
        "posting_date": posting_date,
        "cheque_no": cheque_no,
        "user_remark": user_remark,
    })
    
    if not dimensions:
        dimensions = {}
    
    for entry in entries:
        # Check if this account is a Income or Expense Account
        # If it is, and no cost center is added, select the company default cost center
        cost_center = entry.get("cost_center")

        if not cost_center:
            report_type = frappe.get_cached_value("Account", entry["account"], "report_type")
            if report_type == "Profit and Loss":
                # Cost center is required
                cost_center = default_cost_center
        
        bank_entry.append("accounts", {
            "account": entry["account"],
            # TODO: Multi currency support
            "debit_in_account_currency": entry.get("debit"),
            "credit_in_account_currency": entry.get("credit"),
            "debit": entry.get("debit"),
            "credit": entry.get("credit"),
            "party_type": entry.get("party_type") if entry.get("party") else None,
            "party": entry.get("party"),
            "user_remark": entry.get("user_remark"),
            **entry,
            "cost_center": cost_center
        })

    bank_entry.insert()
    bank_entry.submit()

    if bank_transaction.deposit > 0.0:
        paid_amount = bank_transaction.deposit
    else:
        paid_amount = bank_transaction.withdrawal

    transaction = reconcile_vouchers(bank_transaction_name, json.dumps([{
        "payment_doctype": "Journal Entry",
        "payment_name": bank_entry.name,
        "amount": paid_amount,
    }]), is_new_voucher=True)

    return {
        "transaction": transaction,
        "journal_entry": bank_entry,
    }

# Child table fields of a Payment Entry Reference that the bulk allocation accepts.
# Anything else the client sends is dropped, so a stray field can't be written to the
# payment entry's references.
ALLOWED_REFERENCE_FIELDS = (
    "reference_doctype",
    "reference_name",
    "due_date",
    "bill_no",
    "payment_term",
    "payment_term_outstanding",
    "total_amount",
    "outstanding_amount",
    "allocated_amount",
    "account",
    "exchange_rate",
)


def clean_allocated_references(references: list | None) -> list:
    """
        Keep only the allowed fields of each reference row and drop rows that
        allocate nothing, so an untouched invoice never lands on the payment entry.
    """
    cleaned = []

    for reference in (references or []):
        if flt(reference.get("allocated_amount")) <= 0:
            continue

        if not (reference.get("reference_doctype") and reference.get("reference_name")):
            frappe.throw(_("Every allocated invoice needs a reference document and name"))

        row = {field: reference.get(field) for field in ALLOWED_REFERENCE_FIELDS if reference.get(field) is not None}
        row.setdefault("exchange_rate", 1)
        cleaned.append(row)

    return cleaned


def validate_bulk_allocations(transactions: dict, references_by_transaction: dict):
    """
        Validate the allocations of a bulk payment entry submission before anything is written.

        Two things can go wrong: a transaction allocating more than it is worth, and an
        invoice being allocated beyond its outstanding amount once every transaction in the
        batch is counted. Payment Entry catches the first per document, but only after the
        earlier entries of the batch have already been submitted - so both are checked upfront.
    """
    allocated_per_invoice = {}

    for name, references in references_by_transaction.items():
        transaction = transactions.get(str(name))

        if not transaction:
            frappe.throw(_("Allocations were sent for {0}, which is not part of this reconciliation").format(name))

        total_allocated = sum(flt(reference.get("allocated_amount")) for reference in references)

        if flt(total_allocated - flt(transaction.unallocated_amount), 6) > 0:
            frappe.throw(_("The transaction dated {0} allocates {1} across its invoices, which is more than the {2} left on it").format(
                frappe.format(transaction.date, {"fieldtype": "Date"}),
                total_allocated,
                transaction.unallocated_amount,
            ))

        for reference in references:
            key = (reference.get("reference_doctype"), reference.get("reference_name"))
            allocated_per_invoice[key] = flt(allocated_per_invoice.get(key, 0)) + flt(reference.get("allocated_amount"))

    for (reference_doctype, reference_name), allocated in allocated_per_invoice.items():
        outstanding = flt(frappe.db.get_value(reference_doctype, reference_name, "outstanding_amount"))

        if flt(allocated - outstanding, 6) > 0:
            frappe.throw(_("{0} {1} has {2} outstanding, but the selected transactions allocate {3} to it").format(
                reference_doctype, reference_name, outstanding, allocated,
            ))


@frappe.whitelist(methods=['POST'])
def create_bulk_payment_entry_and_reconcile(bank_transaction_names: list[str | int],
                                            party_type: str,
                                            party: str | int,
                                            account: str,
                                            mode_of_payment: str | None = None,
                                            references: dict | str | None = None):
    """
        Create a payment entry for each bank transaction and reconcile it with that transaction.

        `references` optionally maps a bank transaction name to the invoices its payment entry
        settles - {"<bank transaction>": [{reference_doctype, reference_name, allocated_amount, ...}]}.
        A transaction missing from the map gets a fully unallocated payment entry, which is what
        the whole batch used to get.
    """
    references_by_transaction = frappe.parse_json(references) if isinstance(references, str) else (references or {})

    transactions = {
        str(name): frappe.db.get_value("Bank Transaction", name, ["name", "deposit", "withdrawal", "bank_account", "currency", "unallocated_amount", "date", "reference_number", "description"], as_dict=True)
        for name in bank_transaction_names
    }

    references_by_transaction = {
        str(name): clean_allocated_references(rows)
        for name, rows in references_by_transaction.items()
    }

    validate_bulk_allocations(transactions, references_by_transaction)

    results = []
    errors = []

    for bank_transaction_name in bank_transaction_names:

        def action(bank_transaction_name=bank_transaction_name):
            bank_transaction = transactions[str(bank_transaction_name)]

            transaction_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")
            company = frappe.get_cached_value("Account", transaction_account, "company")

            is_withdrawal = bank_transaction.withdrawal > 0.0

            if is_withdrawal:
                paid_from = transaction_account
                paid_to = account
            else:
                paid_from = account
                paid_to = transaction_account

            payment_entry_doc = frappe.get_doc({
                "doctype": "Payment Entry",
                "payment_type": "Pay" if is_withdrawal else "Receive",
                "bank_account": bank_transaction.bank_account,
                "company": company,
                "mode_of_payment": mode_of_payment,
                "party_type": party_type,
                "party": party,
                "paid_from": paid_from,
                "paid_to": paid_to,
                "paid_amount": bank_transaction.unallocated_amount,
                "base_paid_amount": bank_transaction.unallocated_amount,
                "received_amount": bank_transaction.unallocated_amount,
                "base_received_amount": bank_transaction.unallocated_amount,
                "target_exchange_rate": 1,
                "source_exchange_rate": 1,
                "reference_date": bank_transaction.date,
                "posting_date": bank_transaction.date,
                "reference_no": (bank_transaction.reference_number or bank_transaction.description or '')[:140],
                # The invoices this transaction settles, if any were allocated. Payment Entry derives
                # total_allocated_amount and unallocated_amount from these on validate.
                "references": references_by_transaction.get(str(bank_transaction_name), []),
            })

            payment_entry_doc.insert()
            payment_entry_doc.submit()

            final_transaction = reconcile_vouchers(bank_transaction_name, json.dumps([{
                "payment_doctype": "Payment Entry",
                "payment_name": payment_entry_doc.name,
                "amount": payment_entry_doc.paid_amount,
            }]), is_new_voucher=True)

            return {
                "transaction": final_transaction,
                "payment_entry": payment_entry_doc,
            }

        run_bulk_item(bank_transaction_name, action, results, errors)

    return {
        "results": results,
        "errors": errors,
    }

    
@frappe.whitelist(methods=['POST'])
def create_payment_entry_and_reconcile(bank_transaction_name: str | int, 
                                       payment_entry_doc: dict):
    """
        Create a payment entry and reconcile it with the bank transaction
    """
    payment_entry = frappe.get_doc({
        **payment_entry_doc,
        "doctype": "Payment Entry",
    })
    payment_entry.insert()
    payment_entry.submit()
    transaction = reconcile_vouchers(bank_transaction_name, json.dumps([{
        "payment_doctype": "Payment Entry",
        "payment_name": payment_entry.name,
        "amount": payment_entry.paid_amount,
    }]), is_new_voucher=True)

    return {
        "transaction": transaction,
        "payment_entry": payment_entry,
    }


@frappe.whitelist(methods=['GET'])
def get_account_defaults(account: str):
    """
        Get the default cost center and write off account for an account
    """
    company, report_type = frappe.db.get_value("Account", account, ["company", "report_type"])

    return get_default_cost_center(company) if report_type == "Profit and Loss" else  ""


@frappe.whitelist()
def get_linked_payments(bank_transaction_name, document_types=None, from_date=None,
                        to_date=None, filter_by_reference_date=None,
                        from_amount=None, to_amount=None):
    """
        Wrap ERPNext's get_linked_payments and enrich each result with the
        party's display name (e.g. customer_name / supplier_name) so the
        reconciliation UI can show the name next to the party code.
    """
    payments = _get_linked_payments(
        bank_transaction_name, document_types, from_date, to_date,
        filter_by_reference_date, from_amount, to_amount,
    )
    payments = [p for p in payments if flt(p.get("paid_amount"), 3) > 0]

    name_cache = {}
    for p in payments:
        party_type, party = p.get("party_type"), p.get("party")
        if not (party_type and party):
            continue
        key = (party_type, party)
        if key not in name_cache:
            field = "title" if party_type == "Shareholder" else party_type.lower() + "_name"
            name_cache[key] = frappe.db.get_value(party_type, party, field)
        p["party_name"] = name_cache[key]

    return payments


@frappe.whitelist(methods=["GET"])
def get_party_details(company: str, party_type: str, party: str | int):

    if not frappe.db.exists(party_type, party):
        frappe.throw(_("{0} {1} does not exist").format(party_type, party))

    party_account = get_party_account(party_type, party, company)
    _party_name = "title" if party_type == "Shareholder" else party_type.lower() + "_name"
    party_name = frappe.db.get_value(party_type, party, _party_name)

    return {
        "party_account": party_account,
        "party_name": party_name,
    }

@frappe.whitelist(methods=["GET"])
def search_for_transfer_transaction(transaction_id: str | int):
    """
    When users try to create a transfer, we could help them by searching for the mirror transaction.

    So for a withdrawal of 1000, we could search for a deposit of 1000 on the same date.

    If the mirror transaction is found, we return the bank account and account details.
    """
    company, withdrawal, deposit, date, bank_account = frappe.db.get_value("Bank Transaction", transaction_id, ["company", "withdrawal", "deposit", "date", "bank_account"])

    
    days = frappe.db.get_single_value("Mint Settings", "transfer_match_days")

    if not days:
        days = 4

    min_date = frappe.utils.add_days(date, -days)
    max_date = frappe.utils.add_days(date, days)
    mirror_tx = frappe.db.get_list("Bank Transaction", filters={
        "company": company,
        "date": ["between", [min_date, max_date]],
        "withdrawal": deposit,
        "bank_account": ["!=", bank_account],
        "deposit": withdrawal,
        "docstatus": 1,
        "status": "Unreconciled",
    }, fields=["name", "bank_account", "reference_number", "date", "description", "withdrawal", "deposit", "currency"])

    if len(mirror_tx) == 1:
        return {
            "name": mirror_tx[0].name,
            "reference_number": mirror_tx[0].reference_number,
            "description": mirror_tx[0].description,
            "currency": mirror_tx[0].currency,
            "withdrawal": mirror_tx[0].withdrawal,
            "deposit": mirror_tx[0].deposit,
            "date": mirror_tx[0].date,
            "bank_account": mirror_tx[0].bank_account,
            "account": frappe.get_cached_value("Bank Account", mirror_tx[0].bank_account, "account"),
        }

    return None
