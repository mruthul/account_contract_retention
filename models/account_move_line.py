from odoo import api, fields, models
from odoo.tools import frozendict


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    contract_line_type = fields.Selection(
        selection=[
            ('retention', "Retention"),
            ('advance', "Advance Recovery"),
            ('release', "Retention Release"),
        ],
        string="Contract Line Type",
        readonly=True,
        index='btree_not_null',
    )
    retention_source_line_id = fields.Many2one(
        comodel_name='account.move.line',
        string="Released Retention",
        readonly=True,
        copy=False,
        index='btree_not_null',
        help="Retention line of the invoice released by this journal item.",
    )
    contract_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order (Project)",
        compute='_compute_contract_order_id',
        store=True,
        recursive=True,
        index='btree_not_null',
    )

    @api.depends(
        'move_id.contract_order_id',
        'retention_source_line_id.contract_order_id',
        'payment_id.contract_order_id',
    )
    def _compute_contract_order_id(self):
        for line in self:
            if line.retention_source_line_id:
                line.contract_order_id = line.retention_source_line_id.contract_order_id
            elif line.payment_id:
                line.contract_order_id = line.payment_id.contract_order_id
            else:
                line.contract_order_id = line.move_id.contract_order_id

    @api.depends('contract_line_type', 'account_id')
    def _compute_term_key(self):
        super()._compute_term_key()
        for line in self:
            if line.term_key and line.move_id.is_sale_document(include_receipts=True):
                extra = {'contract_line_type': line.contract_line_type or False}
                if line.contract_line_type:
                    extra['account_id'] = line.account_id.id
                line.term_key = frozendict({**line.term_key, **extra})

    @api.depends('contract_line_type')
    def _compute_account_id(self):
        super()._compute_account_id()
        for line in self.filtered(lambda l: l.display_type == 'payment_term' and l.move_id.is_sale_document(True)):
            move = line.move_id
            if line.contract_line_type == 'retention':
                line.account_id = move.company_id.retention_account_id
            elif line.contract_line_type == 'advance':
                line.account_id = move.advance_payment_id._get_contract_advance_line().account_id or line.account_id
            elif line.account_id and line.account_id == move.company_id.retention_account_id:
                account = move.with_company(move.company_id).commercial_partner_id.property_account_receivable_id
                line.account_id = move.fiscal_position_id.map_account(account) if move.fiscal_position_id else account

    @api.depends('contract_line_type')
    def _compute_name(self):
        super()._compute_name()
        for line in self.filtered(lambda l: l.display_type == 'payment_term' and l.contract_line_type):
            move = line.move_id
            if line.contract_line_type == 'retention':
                line.name = self.env._("Retention %s%%", f"{move.retention_percent:g}")
            elif line.contract_line_type == 'advance':
                line.name = self.env._("Advance recovery - %s", move.advance_payment_id.name or '')
