from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    retention_account_id = fields.Many2one(
        related='company_id.retention_account_id',
        readonly=False,
        domain="[('account_type', '=', 'asset_receivable'), ('reconcile', '=', True), ('company_ids', 'parent_of', company_id)]",
    )
    retention_percent = fields.Float(related='company_id.retention_percent', readonly=False)
    retention_journal_id = fields.Many2one(
        related='company_id.retention_journal_id',
        readonly=False,
        domain="[('type', '=', 'general'), ('company_id', 'parent_of', company_id)]",
    )
