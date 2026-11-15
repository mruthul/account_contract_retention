from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    retention_percent = fields.Float(
        string="Retention %",
        digits=(16, 2),
        default=lambda self: self.env.company.retention_percent,
        copy=True,
    )
    retention_due_date = fields.Date(
        string="Expected Handover",
        copy=False,
        help="Due date given to the retention held on the invoices of this order.",
    )
    advance_recovery_percent = fields.Float(
        string="Advance Recovery %",
        digits=(16, 2),
        help="Share of the work done on each invoice that is recovered from the advance payment.",
    )
    contract_advance_ids = fields.One2many(
        comodel_name='account.payment',
        inverse_name='contract_order_id',
        string="Advance Payments",
    )

    @api.constrains('retention_percent', 'advance_recovery_percent')
    def _check_contract_percentages(self):
        for order in self:
            if not 0 <= order.retention_percent <= 100 or not 0 <= order.advance_recovery_percent <= 100:
                raise ValidationError(self.env._("Retention and advance recovery must be between 0 and 100%."))

    def _prepare_invoice(self):
        values = super()._prepare_invoice()
        values.update({
            'retention_percent': self.retention_percent,
            'retention_due_date': self.retention_due_date,
        })
        if self.advance_recovery_percent:
            values.update({
                'advance_payment_id': self._get_open_contract_advance().id,
                'advance_recovery_percent': self.advance_recovery_percent,
            })
        return values

    def _get_open_contract_advance(self):
        """Oldest advance payment of the order that still has a balance to recover."""
        self.ensure_one()
        return self.contract_advance_ids.filtered(
            lambda p: p.state in ('in_process', 'paid') and p.advance_residual > 0
        ).sorted(lambda p: (p.date, p.id))[:1]
