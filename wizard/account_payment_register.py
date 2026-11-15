from odoo import api, models
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    @api.model
    def default_get(self, fields):
        # Retention is not payable before it is released.
        res = super().default_get(fields)
        if res.get('line_ids') and self.env.context.get('active_model') == 'account.move':
            line_ids = res['line_ids'][0][2]
            lines = self.env['account.move.line'].browse(line_ids)
            payable_lines = lines.filtered(lambda line: line.contract_line_type != 'retention')
            if not payable_lines:
                raise UserError(self.env._(
                    "Only retention is left on these invoices. Release it before registering a payment."
                ))
            res['line_ids'] = [(6, 0, payable_lines.ids)]
        return res
