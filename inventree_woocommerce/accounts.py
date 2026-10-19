"""
Credit accounts: which customers may "charge to account" at checkout.

InvenTree decides. A customer company is an approved shop account when its
company parameter "Shop Account" (configurable) is Yes. An optional "Credit
Limit" parameter caps it. Logins in the shop are matched to the company by
email: the company's own email, or any of its contacts' emails.

For each matching WooCommerce customer the plugin keeps three customer meta
values up to date, which the companion WordPress plugin reads to show or
hide its "Charge to my account" payment method:

    inventree_account_approved   "yes" / "no"
    inventree_credit_available   "" (no limit) or the amount left, e.g. "1250.00"
    inventree_account_company    the InvenTree company name

Credit used = the value of the company's open Sales Orders (pending, in
progress, on hold, shipped-not-closed). Invoices waiting for payment live in
your accounting system, not InvenTree, so they are not counted - set the
limit with that in mind, or close Sales Orders only once they're paid.

The order processor re-checks approval and credit itself (see orders.py), so
a stale value in WooCommerce can't push an order through unchecked: it is
created, but put on hold for a person.
"""

import logging
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)

META_APPROVED = "inventree_account_approved"
META_CREDIT = "inventree_credit_available"
META_COMPANY = "inventree_account_company"

_YES = {"yes", "y", "true", "1", "on", "approved", "account"}


# --- pure helpers ------------------------------------------------------------------

def parse_yes(value):
    return str(value or "").strip().lower() in _YES


def parse_credit_limit(value):
    """
    Credit limit parameter text -> Decimal, or None for no limit.

    Blank / "none" / "unlimited" = no limit. Text that isn't a number is
    treated as a limit of 0 (no credit) - an unreadable limit must not
    become an unlimited one.
    """
    text = str(value or "").strip().lower()
    if text in ("", "none", "unlimited", "no limit", "-"):
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return max(amount, Decimal("0"))


def credit_available(limit, used):
    if limit is None:
        return None
    return max(Decimal(limit) - Decimal(used), Decimal("0"))


def format_credit(value):
    return "" if value is None else str(Decimal(value).quantize(Decimal("0.01")))


def desired_meta(approved, credit, company_name):
    return {
        META_APPROVED: "yes" if approved else "no",
        META_CREDIT: credit if approved else "0.00",
        META_COMPANY: company_name if approved else "",
    }


# --- InvenTree side --------------------------------------------------------------------

def _company_content_type():
    from company.models import Company
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get_for_model(Company)


def approved_accounts(config, company_ids=None):
    """{company_id: credit limit Decimal or None} for every approved account customer."""
    if not config.account_parameter:
        return {}
    from common.models import Parameter

    qs = Parameter.objects.filter(model_type=_company_content_type(),
                                  template__name__iexact=config.account_parameter)
    if company_ids is not None:
        qs = qs.filter(model_id__in=list(company_ids))
    approved = {cid for cid, data in qs.values_list("model_id", "data") if parse_yes(data)}
    if not approved:
        return {}

    from company.models import Company

    approved = set(Company.objects.filter(pk__in=approved, is_customer=True, active=True)
                   .values_list("pk", flat=True))

    limits = {}
    if config.credit_limit_parameter:
        for cid, data in Parameter.objects.filter(
                model_type=_company_content_type(), model_id__in=approved,
                template__name__iexact=config.credit_limit_parameter).values_list("model_id", "data"):
            limits[cid] = parse_credit_limit(data)
    return {cid: limits.get(cid) for cid in approved}


def company_emails(company_ids):
    """{email (lower case): company_id} from each company's email and its contacts' emails."""
    from company.models import Company, Contact

    found = {}
    rows = list(Company.objects.filter(pk__in=company_ids).values_list("email", "pk"))
    rows += list(Contact.objects.filter(company_id__in=company_ids).values_list("email", "company_id"))
    for email, cid in sorted(rows, key=lambda r: r[1]):
        email = (email or "").strip().lower()
        if email and email not in found:  # an email on two companies goes to the lower id, consistently
            found[email] = cid
    return found


def credit_used(company_id, currency, exclude_sales_order_id=None):
    """Value of the company's open Sales Orders, in `currency`."""
    from djmoney.contrib.exchange.models import convert_money
    from order.models import SalesOrder
    from order.status_codes import SalesOrderStatusGroups

    total = Decimal("0")
    orders = SalesOrder.objects.filter(customer_id=company_id, status__in=SalesOrderStatusGroups.OPEN)
    if exclude_sales_order_id:
        orders = orders.exclude(pk=exclude_sales_order_id)
    for so in orders:
        price = so.total_price
        if price is None:
            continue
        if str(price.currency) != currency:
            try:
                price = convert_money(price, currency)
            except Exception:
                logger.warning("No exchange rate to convert Sales Order %s to %s", so.pk, currency)
                continue
        total += Decimal(price.amount)
    return total


def account_status(config, company_id):
    """(approved, limit, available) for one company, available None = no limit."""
    approved = approved_accounts(config, [company_id])
    if company_id not in approved:
        return False, None, None
    limit = approved[company_id]
    return True, limit, credit_available(limit, credit_used(company_id, config.currency))


# --- WooCommerce side ------------------------------------------------------------------

def sync_accounts(config, lookup_new=True):
    """
    Bring every linked WooCommerce login's account meta up to date.

    lookup_new=True also searches WooCommerce for logins belonging to
    approved companies that aren't linked yet (one request per email, so it
    runs on the hourly schedule rather than on every event).
    """
    from company.models import Company

    from .models import CustomerLink
    from .woo_client import WooClient, WooError

    summary = {"linked": 0, "updated": 0, "errors": 0}
    if not config.woo_configured or not config.account_parameter:
        return summary
    client = WooClient.from_config(config)

    approved = approved_accounts(config)
    emails = company_emails(approved)
    links = {link.woo_customer_id: link for link in CustomerLink.objects.all()}
    linked_emails = {link.email.lower() for link in links.values()}

    if lookup_new:
        for email, cid in emails.items():
            if email in linked_emails:
                continue
            try:
                customer = client.find_customer_by_email(email)
            except WooError as exc:
                summary["errors"] += 1
                logger.warning("Account lookup for %s failed: %s", email, exc)
                continue
            if customer:
                link, _ = CustomerLink.objects.update_or_create(
                    woo_customer_id=customer["id"], defaults={"email": email, "company_id": cid})
                links[link.woo_customer_id] = link
                summary["linked"] += 1

    names = dict(Company.objects.filter(pk__in=approved).values_list("pk", "name"))
    used_cache = {}
    updates, pending = [], []
    for link in links.values():
        cid = emails.get(link.email.lower(), link.company_id)
        is_approved = cid in approved
        credit = ""
        if is_approved:
            if cid not in used_cache:
                used_cache[cid] = credit_used(cid, config.currency)
            credit = format_credit(credit_available(approved[cid], used_cache[cid]))
        name = names.get(cid, "")[:100] if is_approved else ""
        meta = desired_meta(is_approved, credit, name)
        unchanged = (link.approved_pushed == is_approved and link.credit_pushed == meta[META_CREDIT]
                     and link.company_name_pushed == meta[META_COMPANY] and link.company_id == cid)
        if unchanged:
            continue
        link.company_id = cid
        updates.append({"id": link.woo_customer_id,
                        "meta_data": [{"key": k, "value": v} for k, v in meta.items()]})
        pending.append((link, is_approved, meta))

    if updates:
        try:
            results = client.batch("customers", update=updates)["update"]
        except WooError as exc:
            summary["errors"] += len(updates)
            logger.warning("Account update failed: %s", exc)
            return summary
        now = timezone.now()
        for (link, is_approved, meta), result in zip(pending, results, strict=True):
            if result.get("error"):
                summary["errors"] += 1
                if result["error"].get("code") == "woocommerce_rest_invalid_id":
                    link.delete()  # login deleted in WordPress
                continue
            link.approved_pushed = is_approved
            link.credit_pushed = meta[META_CREDIT]
            link.company_name_pushed = meta[META_COMPANY]
            link.last_synced_at = now
            link.save()
            summary["updated"] += 1
    return summary


def link_customer(woo_customer_id, email, company_id):
    """Remember which company a shop login belongs to (from an order)."""
    from .models import CustomerLink

    if not woo_customer_id:
        return
    CustomerLink.objects.update_or_create(
        woo_customer_id=int(woo_customer_id),
        defaults={"email": (email or "").strip().lower()[:254], "company_id": company_id},
    )


def queue_account_sync():
    from InvenTree.tasks import offload_task

    offload_task("inventree_woocommerce.tasks.sync_account_links", group="plugin", check_duplicates=True)
