from odoo import api, fields, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    is_contract_advance = fields.Boolean(
        string="Contract Advance",
        copy=False,
        help="Advance received from the customer before the work starts. "
             "It is recovered progressively from the customer invoices.",
    )
    contract_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order",
        copy=False,
        index='btree_not_null',
        check_company=True,
        domain="[('partner_id', 'child_of', partner_id), ('state', '=', 'sale')]",
    )
    advance_residual = fields.Monetary(
        string="Advance Balance",
        compute='_compute_advance_residual',
        currency_field='currency_id',
    )

    @api.depends('move_id.line_ids.amount_residual', 'move_id.line_ids.amount_residual_currency')
    def _compute_advance_residual(self):
        for payment in self:
            line = payment._get_contract_advance_line()
            if not line:
                payment.advance_residual = 0.0
            elif line.currency_id == payment.currency_id:
                payment.advance_residual = -line.amount_residual_currency
            else:
                payment.advance_residual = -line.amount_residual

    def action_post(self):
        # Without an outstanding account, Odoo only books the payment when the bank
        # statement is reconciled. An advance must hit the customer account right
        # away, since the invoices recover from it.
        for payment in self.filtered(lambda p: p.is_contract_advance and not p.outstanding_account_id):
            payment.outstanding_account_id = payment._get_outstanding_account(payment.payment_type)
        return super().action_post()

    def _get_contract_advance_line(self):
        """The receivable line of the payment, i.e. the credit the advance is recovered from."""
        self.ensure_one()
        if not self.move_id or self.payment_type != 'inbound':
            return self.env['account.move.line']
        counterpart_lines = self._seek_for_lines()[1]
        return counterpart_lines.filtered(lambda line: line.account_type == 'asset_receivable')[:1]
