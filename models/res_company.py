from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    retention_account_id = fields.Many2one(
        comodel_name='account.account',
        string="Retention Receivable Account",
        domain="[('account_type', '=', 'asset_receivable')]",
    )
    retention_percent = fields.Float(string="Default Retention %", digits=(16, 2))
    retention_journal_id = fields.Many2one(
        comodel_name='account.journal',
        string="Retention Release Journal",
        domain="[('type', '=', 'general')]",
    )

    @api.constrains('retention_account_id')
    def _check_retention_account_id(self):
        for company in self:
            account = company.retention_account_id
            if account and (account.account_type != 'asset_receivable' or not account.reconcile):
                raise ValidationError(self.env._(
                    "The retention account must be a reconcilable receivable account."
                ))

    @api.constrains('retention_percent')
    def _check_retention_percent(self):
        for company in self:
            if not 0 <= company.retention_percent <= 100:
                raise ValidationError(self.env._("The default retention must be between 0 and 100%."))
