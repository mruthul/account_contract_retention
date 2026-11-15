# Contract Retention and Advance Recovery

Odoo 19 Enterprise module for contracting companies that invoice progress
monthly, hold a retention on each invoice and recover an advance payment over
the life of the project.

## Configuration

*Accounting > Configuration > Settings > Contract Retention*

- **Receivable Account**: a reconcilable account of type *Receivable* used only
  for retention (e.g. `102199 Retention Receivable`).
- **Default %**: retention proposed on new sales orders and customer invoices.
- **Release Journal**: miscellaneous journal used for the release entries.
  Defaults to the first miscellaneous journal of the company.

## Usage

1. **Advance.** Register the customer payment as usual and tick *Contract
   Advance*. Link it to the sales order when the advance belongs to a project.
2. **Sales order.** On the *Retention & Advance* tab set the retention %, the
   expected handover date and the advance recovery %.
3. **Invoice.** Invoices created from the order inherit these values. On a
   manual invoice they can be entered on the *Retention & Advance* tab. The
   recovery is either a % of the untaxed amount or a fixed amount.
4. **Posting.** The receivable is split in three journal items:

   | Account              | Amount  | Due date          | Status                          |
   |----------------------|---------|-------------------|---------------------------------|
   | Receivable           | 85,000  | payment terms     | open, payable by the customer   |
   | Receivable           | 20,000  | invoice date      | matched with the advance        |
   | Retention Receivable | 10,000  | expected handover | held until released             |

5. **Release.** *Accounting > Customers > Release Retention*, or the button on
   the invoice. Select the customer (and optionally the order), the date and the
   amount; the amount is spread over the invoices oldest first and can be
   adjusted per invoice. The entry moves the amount from Retention Receivable to
   the customer receivable, due on the release date.
6. **Report.** *Accounting > Reporting > Retention and Advances*, as at any
   date, per customer and sales order, with the standard PDF/XLSX export.

## Design notes

- The retention and advance lines are generated through the standard
  `needed_terms` mechanism, the same one that builds the payment term
  installments. They follow every change of the draft invoice (amounts,
  currency, dates, payment terms) and nothing is created on top of the invoice
  at posting time. The extra keys (`contract_line_type`, `account_id`) are
  added to the installment keys of customer documents only.
- The retention account is a receivable account, so the retention stays in the
  partner ledger, the aged receivable (as *not due* until the handover date)
  and the follow-up. It is excluded from the invoice *Amount Due* and payment
  status, and from the *Register Payment* wizard, because the customer does not
  owe it before it is released.
- The advance recovery line is reconciled with the advance payment when the
  invoice is posted. Reconciliation only, no entry is modified.
- Reset to draft keeps the reconciliations (standard Odoo 19 behaviour). On the
  next posting the lines are only regenerated if the invoice changed, so
  posting again never duplicates anything. Resetting to draft is blocked once
  part of the retention has been released: the release has to be reversed
  first.
- Credit notes take the same retention % and a share of the recovered advance
  proportional to the untaxed amount, limited to what is still held/recovered.
  On posting, the retention is matched against the invoice retention and the
  recovery line of the invoice is re-matched so that the returned share goes
  back to the advance payment.
- A full reversal (*Reverse and Cancel*) is refused once retention has been
  released, because Odoo unreconciles the invoice before reversing it and the
  release entry would be left unmatched.
- Releases and advance recoveries in a currency different from the company
  currency go through standard Odoo reconciliation, which books the exchange
  difference.
- Lock dates: the release wizard refuses a date in a locked period; all other
  entries are posted through the standard flow, which enforces the lock dates.

## Journal entries

Example of the brief: advance 200,000, then a progress invoice of 100,000 +
15% VAT with 10% retention and 20% advance recovery.

1. **Advance** (standard customer payment, flagged *Contract Advance*)

   | Account                     | Debit   | Credit  |
   |-----------------------------|---------|---------|
   | Outstanding Receipts (bank) | 200,000 |         |
   | Receivable (customer)       |         | 200,000 |

2. **Progress invoice** (receivable split by the module)

   | Account              | Debit  | Credit  | Due date          |
   |----------------------|--------|---------|-------------------|
   | Receivable           | 85,000 |         | payment terms     |
   | Receivable           | 20,000 |         | invoice date (matched with the advance) |
   | Retention Receivable | 10,000 |         | expected handover |
   | Sales                |        | 100,000 |                   |
   | VAT Output 15%       |        | 15,000  |                   |

3. **Credit note**: mirror of the invoice. Crediting 50,000 returns 5,000
   retention and 10,000 to the advance.

4. **Retention release** (Release Journal)

   | Account               | Debit  | Credit | Due date     |
   |-----------------------|--------|--------|--------------|
   | Receivable (customer) | 10,000 |        | release date |
   | Retention Receivable  |        | 10,000 |              |

   The credit is reconciled with the invoice retention line.

5. **Foreign currency**: standard reconciliation books the exchange difference
   on release and recovery.

## Assumptions

- *Project* is the sales order of the invoice. An invoice with lines from more
  than one sales order is attached to the first one. Invoices without a sales
  order are reported under the customer only.
- Retention and recovery are computed on the untaxed amount (work done). VAT is
  charged on the full amount, as in the brief.
- An advance is a customer payment flagged *Contract Advance* that has a
  journal entry (the bank journal has an outstanding receipts account).
- Down payment invoices created from the sales order carry no retention.
- Early payment discounts are not supported together with retention or
  advance recovery; posting such an invoice raises an error.
- *Total Invoiced* in the report is the VAT-inclusive total, net of credit
  notes.
- An invoice cannot recover more than the advance balance; posting is refused
  with the amount still available.

## Out of scope

- **ZATCA.** The e-invoice XML is not changed: it carries the full invoice
  amount. Retention is not a discount and cannot reduce the payable amount of
  the e-invoice.
- **VAT on advances.** Under KSA VAT, an advance payment is normally a taxable
  event and is invoiced (prepayment invoice) when received. The brief records
  the advance as a plain payment, so this module does not handle VAT on the
  advance. Supporting it would mean recovering from a down payment invoice
  instead of a payment.
- **Aged receivable.** The unused advance shows as an open credit on the
  customer until it is used up (standard Odoo for an unreconciled payment).
