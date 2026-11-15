from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class AccountRetentionRelease(models.TransientModel):
    _name = 'account.retention.release'
    _description = "Release Retention"
    _check_company_auto = True

    company_id = fields.Many2one(
        comodel_name='res.company',
        required=True,
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Customer",
        required=True,
        domain="[('parent_id', '=', False)]",
    )
    order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order",
        check_company=True,
        domain="[('partner_id', 'child_of', partner_id)]",
        help="Leave empty to release retention on any invoice of the customer.",
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    date = fields.Date(required=True, default=fields.Date.context_today)
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        compute='_compute_journal_id',
        store=True,
        precompute=True,
        readonly=False,
        required=True,
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
    amount = fields.Monetary(
        string="Amount to Release",
        currency_field='currency_id',
        help="Spread over the invoices below, oldest first. You can also set the amount per invoice.",
    )
    amount_held = fields.Monetary(compute='_compute_amount_held', currency_field='currency_id')
    line_ids = fields.One2many(
        comodel_name='account.retention.release.line',
        inverse_name='wizard_id',
        compute='_compute_line_ids',
        store=True,
        readonly=False,
    )

    @api.depends('company_id')
    def _compute_journal_id(self):
        for wizard in self:
            wizard.journal_id = wizard.company_id.retention_journal_id or self.env['account.journal'].search([
                *self.env['account.journal']._check_company_domain(wizard.company_id),
                ('type', '=', 'general'),
            ], limit=1)

    @api.depends('company_id', 'partner_id', 'order_id', 'currency_id')
    def _compute_line_ids(self):
        restrict_move_ids = self.env.context.get('retention_move_ids')
        for wizard in self:
            commands = [Command.clear()]
            if wizard.partner_id and wizard.currency_id:
                domain = wizard._get_retention_lines_domain()
                if restrict_move_ids:
                    domain.append(('move_id', 'in', restrict_move_ids))
                retention_lines = self.env['account.move.line'].search(domain, order='date_maturity, date, id')
                commands += [Command.create({'move_line_id': line.id}) for line in retention_lines]
            wizard.line_ids = commands

    @api.depends('line_ids.amount_held')
    def _compute_amount_held(self):
        for wizard in self:
            wizard.amount_held = sum(wizard.line_ids.mapped('amount_held'))

    @api.onchange('amount')
    def _onchange_amount(self):
        self._allocate_amount()

    def action_release(self):
        self.ensure_one()
        if self.amount and self.currency_id.is_zero(sum(self.line_ids.mapped('release_amount'))):
            self._allocate_amount()
        lines = self.line_ids.filtered(lambda line: not self.currency_id.is_zero(line.release_amount))
        self._check_release(lines)

        move = self.env['account.move'].create(self._prepare_move_vals(lines))
        move.action_post()
        for release_line in move.line_ids.filtered(lambda line: line.contract_line_type == 'release'):
            (release_line + release_line.retention_source_line_id).reconcile()

        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Retention Release"),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }

    def _get_retention_lines_domain(self):
        self.ensure_one()
        domain = [
            ('company_id', '=', self.company_id.id),
            ('partner_id', 'child_of', self.partner_id.id),
            ('currency_id', '=', self.currency_id.id),
            ('contract_line_type', '=', 'retention'),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
            ('amount_residual_currency', '>', 0),
        ]
        if self.order_id:
            domain.append(('contract_order_id', '=', self.order_id.id))
        return domain

    def _allocate_amount(self):
        remaining = self.amount
        for line in self.line_ids:
            line.release_amount = min(line.amount_held, max(remaining, 0.0))
            remaining -= line.release_amount

    def _check_release(self, lines):
        self.ensure_one()
        if not self.company_id.retention_account_id:
            raise UserError(self.env._("Set the Retention Receivable account in the accounting settings first."))
        if not lines:
            raise UserError(self.env._("Set the amount to release."))
        for line in lines:
            if self.currency_id.compare_amounts(line.release_amount, 0.0) < 0:
                raise UserError(self.env._("The amount to release cannot be negative."))
            if self.currency_id.compare_amounts(line.release_amount, line.amount_held) > 0:
                raise UserError(self.env._(
                    "You cannot release more than the retention held on %s.", line.move_id.name,
                ))
        if self.currency_id.compare_amounts(self.amount, self.amount_held) > 0:
            raise UserError(self.env._("The amount to release is higher than the retention held."))
        lock_date = self.company_id._get_user_fiscal_lock_date(self.journal_id)
        if self.date <= lock_date:
            raise UserError(self.env._(
                "The release date must be after the lock date (%s).", fields.Date.to_string(lock_date),
            ))

    def _prepare_move_vals(self, lines):
        self.ensure_one()
        company = self.company_id
        line_commands = []
        for line in lines:
            source = line.move_line_id
            balance = self.currency_id._convert(line.release_amount, company.currency_id, company, self.date)
            receivable = source.move_id.line_ids.filtered(
                lambda l: l.display_type == 'payment_term' and not l.contract_line_type
            )[:1].account_id or source.partner_id.with_company(company).property_account_receivable_id
            label = self.env._("Retention release - %s", source.move_id.name)
            common = {
                'name': label,
                'partner_id': source.partner_id.id,
                'currency_id': self.currency_id.id,
                'retention_source_line_id': source.id,
            }
            line_commands += [
                Command.create({
                    **common,
                    'account_id': source.account_id.id,
                    'contract_line_type': 'release',
                    'amount_currency': -line.release_amount,
                    'balance': -balance,
                }),
                Command.create({
                    **common,
                    'account_id': receivable.id,
                    'amount_currency': line.release_amount,
                    'balance': balance,
                    'date_maturity': self.date,
                }),
            ]
        return {
            'move_type': 'entry',
            'company_id': company.id,
            'journal_id': self.journal_id.id,
            'date': self.date,
            'ref': self.env._("Retention release"),
            'line_ids': line_commands,
        }


class AccountRetentionReleaseLine(models.TransientModel):
    _name = 'account.retention.release.line'
    _description = "Release Retention Line"
    _order = 'date_maturity, id'

    wizard_id = fields.Many2one(comodel_name='account.retention.release', required=True, ondelete='cascade')
    move_line_id = fields.Many2one(comodel_name='account.move.line', required=True, readonly=True)
    move_id = fields.Many2one(related='move_line_id.move_id', string="Invoice")
    order_id = fields.Many2one(related='move_line_id.contract_order_id', string="Sales Order")
    date_maturity = fields.Date(related='move_line_id.date_maturity', string="Due Date", store=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    amount_held = fields.Monetary(
        string="Held",
        compute='_compute_amount_held',
        currency_field='currency_id',
    )
    release_amount = fields.Monetary(string="To Release", currency_field='currency_id')

    @api.depends('move_line_id.amount_residual_currency')
    def _compute_amount_held(self):
        for line in self:
            line.amount_held = line.move_line_id.amount_residual_currency
