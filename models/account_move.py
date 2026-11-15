from datetime import date

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import frozendict


class AccountMove(models.Model):
    _inherit = 'account.move'

    contract_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order (Project)",
        compute='_compute_contract_order_id',
        store=True,
        recursive=True,
        index='btree_not_null',
    )

    retention_percent = fields.Float(
        string="Retention %",
        digits=(16, 2),
        compute='_compute_retention_percent',
        store=True,
        readonly=False,
        precompute=True,
        copy=False,
        tracking=True,
    )
    retention_amount = fields.Monetary(
        string="Retention",
        compute='_compute_retention_amount',
        store=True,
        currency_field='currency_id',
    )
    retention_due_date = fields.Date(
        string="Retention Due Date",
        copy=False,
        help="Expected handover date. The retention becomes due from this date once released.",
    )
    retention_held = fields.Monetary(
        string="Retention Held",
        compute='_compute_retention_held',
        currency_field='currency_id',
        help="Retention of this invoice not yet released or credited.",
    )

    advance_payment_id = fields.Many2one(
        comodel_name='account.payment',
        string="Advance Payment",
        copy=False,
        check_company=True,
        domain="[('is_contract_advance', '=', True),"
               " ('partner_id', 'child_of', commercial_partner_id),"
               " ('state', 'in', ('in_process', 'paid')), ('move_id', '!=', False),"
               " ('contract_order_id', 'in', [contract_order_id, False])]",
    )
    advance_recovery_method = fields.Selection(
        selection=[('percent', "Percentage"), ('fixed', "Fixed Amount")],
        string="Recovery Method",
        default='percent',
        copy=False,
    )
    advance_recovery_percent = fields.Float(
        string="Recovery %",
        digits=(16, 2),
        copy=False,
    )
    advance_recovery_fixed = fields.Monetary(
        string="Recovery Amount",
        currency_field='currency_id',
        copy=False,
    )
    advance_recovery_amount = fields.Monetary(
        string="Advance Recovery",
        compute='_compute_advance_recovery_amount',
        store=True,
        currency_field='currency_id',
    )
    advance_available = fields.Monetary(
        string="Advance Available",
        compute='_compute_advance_available',
        currency_field='currency_id',
    )
    amount_payable_now = fields.Monetary(
        string="Amount to Pay Now",
        compute='_compute_amount_payable_now',
        currency_field='currency_id',
    )

    # COMPUTE METHODS
    @api.depends('move_type', 'invoice_line_ids.sale_line_ids', 'reversed_entry_id.contract_order_id')
    def _compute_contract_order_id(self):
        for move in self:
            if not move.is_sale_document():
                move.contract_order_id = False
            elif move.reversed_entry_id:
                move.contract_order_id = move.reversed_entry_id.contract_order_id
            else:
                move.contract_order_id = move.invoice_line_ids.sale_line_ids.order_id[:1]

    @api.depends('move_type', 'company_id')
    def _compute_retention_percent(self):
        # Only a default: invoices from a sales order and credit notes get their
        # value explicitly (see sale.order._prepare_invoice and _reverse_moves).
        for move in self:
            move.retention_percent = move.company_id.retention_percent if move.move_type == 'out_invoice' else 0.0

    @api.depends('retention_percent', 'amount_untaxed', 'reversed_entry_id')
    def _compute_retention_amount(self):
        for move in self:
            if not move.currency_id or not move.retention_percent or move._is_downpayment():
                move.retention_amount = 0.0
                continue
            amount = move.currency_id.round(move.amount_untaxed * move.retention_percent / 100.0)
            origin = move.reversed_entry_id
            if move.move_type == 'out_refund' and origin and move.state == 'draft':
                amount = min(amount, origin.retention_held)
            move.retention_amount = amount

    @api.depends('line_ids.amount_residual_currency', 'line_ids.contract_line_type')
    def _compute_retention_held(self):
        for move in self:
            lines = move.line_ids.filtered(lambda line: line.contract_line_type == 'retention')
            move.retention_held = abs(sum(lines.mapped('amount_residual_currency')))

    @api.depends(
        'advance_payment_id', 'advance_recovery_method', 'advance_recovery_percent',
        'advance_recovery_fixed', 'amount_untaxed', 'reversed_entry_id',
    )
    def _compute_advance_recovery_amount(self):
        for move in self:
            amount = 0.0
            if not move.advance_payment_id or not move.currency_id or move._is_downpayment():
                pass
            elif move.move_type == 'out_refund':
                origin = move.reversed_entry_id
                if origin.amount_untaxed:
                    amount = move.currency_id.round(
                        origin.advance_recovery_amount * move.amount_untaxed / origin.amount_untaxed
                    )
                    if move.state == 'draft':
                        amount = min(amount, origin._get_advance_returnable())
            elif move.advance_recovery_method == 'fixed':
                amount = move.advance_recovery_fixed
            else:
                amount = move.currency_id.round(move.amount_untaxed * move.advance_recovery_percent / 100.0)
            move.advance_recovery_amount = amount

    @api.depends('advance_payment_id', 'currency_id', 'invoice_currency_rate')
    def _compute_advance_available(self):
        for move in self:
            move.advance_available = move._get_advance_available() if move.advance_payment_id else 0.0

    @api.depends('amount_total', 'retention_amount', 'advance_recovery_amount')
    def _compute_amount_payable_now(self):
        for move in self:
            move.amount_payable_now = move.amount_total - move.retention_amount - move.advance_recovery_amount

    def _compute_amount(self):
        super()._compute_amount()
        for move in self.filtered(lambda m: m.is_invoice(include_receipts=True)):
            retention_lines = move.line_ids.filtered(lambda line: line.contract_line_type == 'retention')
            if retention_lines:
                move.amount_residual += move.direction_sign * sum(retention_lines.mapped('amount_residual_currency'))
                move.amount_residual_signed -= sum(retention_lines.mapped('amount_residual'))

    @api.depends('retention_amount', 'retention_due_date', 'advance_recovery_amount', 'advance_payment_id')
    def _compute_needed_terms(self):
        super()._compute_needed_terms()
        for move in self:
            if move.needed_terms and move.is_sale_document(include_receipts=True):
                move.needed_terms = move._get_contract_needed_terms(move.needed_terms)

    def _compute_invoice_date_due(self):
        super()._compute_invoice_date_due()
        for move in self:
            if move.needed_terms and any(key.get('contract_line_type') == 'retention' for key in move.needed_terms):
                move.invoice_date_due = max(
                    (key['date_maturity'] for key in move.needed_terms
                     if key.get('contract_line_type') != 'retention' and key['date_maturity']),
                    default=move.invoice_date_due,
                )

    # ACTION METHODS
    def button_draft(self):
        released = self.filtered(lambda move: move._has_released_retention())
        if released:
            raise UserError(self.env._(
                "Retention has already been released on %s. Reverse the release entry before resetting to draft.",
                ', '.join(released.mapped('name')),
            ))
        return super().button_draft()

    def action_release_retention(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Release Retention"),
            'res_model': 'account.retention.release',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_company_id': self.company_id.id,
                'default_partner_id': self.commercial_partner_id.id,
                'default_currency_id': self.currency_id.id,
                'default_order_id': self.contract_order_id.id,
                'retention_move_ids': self.ids,
            },
        }

    # BUSINESS METHODS
    def _post(self, soft=True):
        contract_moves = self.filtered(lambda move: move.is_sale_document() and (
            move.retention_amount or move.advance_recovery_amount or any(move.line_ids.mapped('contract_line_type'))
        ))
        contract_moves._sync_contract_terms()
        contract_moves._check_contract_deductions()
        posted = super()._post(soft=soft)
        posted.filtered(lambda move: move.move_type == 'out_invoice')._reconcile_advance_recovery()
        return posted

    def _reverse_moves(self, default_values_list=None, cancel=False):
        default_values_list = default_values_list or [{} for _move in self]
        for move, default_values in zip(self, default_values_list):
            if move.move_type == 'out_invoice' and (move.retention_amount or move.advance_recovery_amount):
                default_values.setdefault('retention_percent', move.retention_percent)
                default_values.setdefault('retention_due_date', move.retention_due_date)
                if move.advance_recovery_amount:
                    default_values.setdefault('advance_payment_id', move.advance_payment_id.id)
        if cancel:
            released = self.filtered(lambda move: move._has_released_retention())
            if released:
                raise UserError(self.env._(
                    "Retention has already been released on %s. Reverse the release entry first, "
                    "or issue a credit note without cancelling the invoice.",
                    ', '.join(released.mapped('name')),
                ))
        return super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)

    def _reconcile_reversed_moves(self, reverse_moves, move_reverse_cancel):
        for credit_note in reverse_moves.filtered(lambda move: move.move_type == 'out_refund'):
            credit_note.reversed_entry_id._return_advance_recovery(credit_note)
        return super()._reconcile_reversed_moves(reverse_moves, move_reverse_cancel)

    def _get_contract_lines(self, line_type):
        return self.line_ids.filtered(lambda line: line.contract_line_type == line_type)

    def _get_contract_needed_terms(self, needed_terms):
        """Split the receivable of the invoice between the regular installments,
        the retention line and the advance recovery line.

        The deductions are taken from the regular installments pro rata, the last
        installment absorbing the rounding difference.
        """
        self.ensure_one()
        regular_terms = {
            frozendict({**key, 'contract_line_type': False}): dict(values)
            for key, values in needed_terms.items()
        }

        deductions = []
        retention_account = self.company_id.retention_account_id
        if retention_account and not self.currency_id.is_zero(self.retention_amount):
            last_date = max((key['date_maturity'] for key in regular_terms if key['date_maturity']), default=False)
            due_date = self.retention_due_date or last_date
            deductions.append(('retention', self.retention_amount, retention_account, due_date))
        advance_line = self.advance_payment_id._get_contract_advance_line() if self.advance_payment_id else None
        if advance_line and not self.currency_id.is_zero(self.advance_recovery_amount):
            recovery_date = self.invoice_date or self.date or fields.Date.context_today(self)
            deductions.append(('advance', self.advance_recovery_amount, advance_line.account_id, recovery_date))
        if not deductions:
            return regular_terms

        total_currency = sum(values['amount_currency'] for values in regular_terms.values())
        total_balance = sum(values['balance'] for values in regular_terms.values())
        if self.currency_id.is_zero(total_currency):
            return regular_terms

        sign = -self.direction_sign
        contract_terms = {}
        deducted_currency = deducted_balance = 0.0
        for line_type, amount, account, date_maturity in deductions:
            amount_currency = sign * amount
            balance = self.company_currency_id.round(total_balance * amount_currency / total_currency)
            key = frozendict({
                'move_id': self.id,
                'date_maturity': date_maturity,
                'discount_date': False,
                'contract_line_type': line_type,
                'account_id': account.id,
            })
            contract_terms[key] = {'balance': balance, 'amount_currency': amount_currency}
            deducted_currency += amount_currency
            deducted_balance += balance

        keys = sorted(regular_terms, key=lambda key: key['date_maturity'] or date.max)
        remaining_currency, remaining_balance = deducted_currency, deducted_balance
        for index, key in enumerate(keys):
            values = regular_terms[key]
            if index == len(keys) - 1:
                share_currency, share_balance = remaining_currency, remaining_balance
            else:
                ratio = values['amount_currency'] / total_currency
                share_currency = self.currency_id.round(deducted_currency * ratio)
                share_balance = self.company_currency_id.round(deducted_balance * ratio)
            values['amount_currency'] -= share_currency
            values['balance'] -= share_balance
            remaining_currency -= share_currency
            remaining_balance -= share_balance

        regular_terms = {
            key: values for key, values in regular_terms.items()
            if not (self.currency_id.is_zero(values['amount_currency'])
                    and self.company_currency_id.is_zero(values['balance']))
        }
        return {**regular_terms, **contract_terms}

    def _sync_contract_terms(self):
        """Make sure the receivable lines reflect the current deductions.
        Needed when the configuration changed after the lines were generated, e.g.
        the retention account was set after the invoice was created.
        """
        with self._sync_dynamic_line(
            existing_key_fname='term_key',
            needed_vals_fname='needed_terms',
            needed_dirty_fname='needed_terms_dirty',
            line_type='payment_term',
            container={'records': self},
        ):
            self._compute_needed_terms()

    def _check_contract_deductions(self):
        for move in self:
            if not move.retention_amount and not move.advance_recovery_amount:
                continue
            currency = move.currency_id
            if move.retention_amount:
                if not move.company_id.retention_account_id:
                    raise UserError(self.env._(
                        "Set the Retention Receivable account in the accounting settings of %s "
                        "before posting invoices with retention.",
                        move.company_id.name,
                    ))
                if not move.retention_due_date:
                    raise UserError(self.env._(
                        "Set the retention due date (expected handover) on %s.", move.display_name,
                    ))
            if move.advance_recovery_amount:
                if currency.compare_amounts(move.advance_recovery_amount, 0.0) < 0:
                    raise UserError(self.env._("The advance recovery cannot be negative."))
                if not move.advance_payment_id._get_contract_advance_line():
                    raise UserError(self.env._(
                        "The advance payment %s has no journal entry on the customer account yet.",
                        move.advance_payment_id.display_name,
                    ))
                if move.move_type == 'out_invoice':
                    available = move._get_advance_available()
                    if currency.compare_amounts(move.advance_recovery_amount, available) > 0:
                        raise UserError(self.env._(
                            "You cannot recover %(amount)s from %(payment)s: "
                            "only %(available)s is left on this advance.",
                            amount=currency.format(move.advance_recovery_amount),
                            payment=move.advance_payment_id.display_name,
                            available=currency.format(available),
                        ))
            if currency.compare_amounts(move.retention_amount + move.advance_recovery_amount, move.amount_total) > 0:
                raise UserError(self.env._(
                    "The retention and advance recovery of %s exceed the invoice total.", move.display_name,
                ))
            if move.invoice_payment_term_id.early_discount:
                raise UserError(self.env._(
                    "Early payment discounts cannot be combined with retention or advance recovery (%s).",
                    move.display_name,
                ))

    def _get_advance_available(self):
        """Balance of the selected advance payment, in the invoice currency.

        What this invoice already recovered is added back, so that the check
        still passes when the invoice is reset to draft and posted again.
        """
        self.ensure_one()
        source = self.advance_payment_id._get_contract_advance_line()
        if not source:
            return 0.0
        partials = self._origin._get_contract_lines('advance').matched_credit_ids.filtered(
            lambda partial: partial.credit_move_id == source
        )
        if source.currency_id == self.currency_id:
            return -source.amount_residual_currency + sum(partials.mapped('credit_amount_currency'))
        available = -source.amount_residual + sum(partials.mapped('amount'))
        if self.currency_id == self.company_currency_id:
            return available
        return self.currency_id.round(available * (self.invoice_currency_rate or 1.0))

    def _get_advance_returnable(self):
        """Advance recovered by this invoice that has not been returned by a credit note yet."""
        self.ensure_one()
        returned = sum(
            self.reversal_move_ids.filtered(lambda move: move.state == 'posted').mapped('advance_recovery_amount')
        )
        return max(self.advance_recovery_amount - returned, 0.0)

    def _reconcile_advance_recovery(self):
        for move in self:
            recovery_line = move._get_contract_lines('advance').filtered(lambda line: not line.reconciled)
            if not recovery_line:
                continue
            source = move.advance_payment_id._get_contract_advance_line()
            (recovery_line + source).reconcile()
            if not recovery_line.reconciled:
                raise UserError(self.env._(
                    "Not enough balance left on the advance payment %s.", move.advance_payment_id.display_name,
                ))

    def _return_advance_recovery(self, credit_note):
        """Give back to the advance payment the share of the advance cancelled by a credit note.
        The invoice recovery line is matched with the credit note first; whatever is
        left of it is matched again with the advance payment.
        """
        self.ensure_one()
        returned_line = credit_note._get_contract_lines('advance').filtered(lambda line: not line.reconciled)
        recovery_line = self._get_contract_lines('advance')
        if not returned_line or not recovery_line:
            return
        source = self.advance_payment_id._get_contract_advance_line()
        recovery_line.matched_credit_ids.filtered(lambda partial: partial.credit_move_id == source).unlink()
        (recovery_line + returned_line).reconcile()
        if source and not recovery_line.reconciled:
            (recovery_line + source).reconcile()

    def _has_released_retention(self):
        self.ensure_one()
        retention_lines = self._get_contract_lines('retention')
        counterparts = (
            retention_lines.matched_debit_ids.debit_move_id
            | retention_lines.matched_credit_ids.credit_move_id
        )
        return any(line.contract_line_type == 'release' for line in counterparts)

    def _get_contract_deduction_lines(self):
        """Lines displayed under the totals of the printed invoice."""
        self.ensure_one()
        lines = []
        if self.retention_amount:
            lines.append({
                'label': self.env._("Less: Retention (%s%%)", f"{self.retention_percent:g}"),
                'amount': -self.retention_amount,
            })
        if self.advance_recovery_amount:
            lines.append({
                'label': self.env._("Less: Advance Recovery"),
                'amount': -self.advance_recovery_amount,
            })
        return lines
