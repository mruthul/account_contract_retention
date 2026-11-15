from collections import defaultdict

from odoo import models
from odoo.tools import SQL


class ContractRetentionReportHandler(models.AbstractModel):
    _name = 'account.contract.retention.report.handler'
    _inherit = ['account.report.custom.handler']
    _description = "Retention and Advances Report Custom Handler"

    _COLUMNS = (
        'invoiced',
        'retention_held',
        'retention_released',
        'retention_balance',
        'advance_received',
        'advance_recovered',
        'advance_balance',
    )

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        results = self._query_values(report, options)

        partners = self.env['res.partner'].browse({partner_id for partner_id, _order_id in results}).sorted(
            lambda p: (p.name or '').lower()
        )
        orders_by_partner = defaultdict(list)
        for partner_id, order_id in results:
            orders_by_partner[partner_id].append(order_id)

        lines = []
        totals = defaultdict(lambda: defaultdict(float))
        for partner in partners:
            partner_values = defaultdict(lambda: defaultdict(float))
            order_lines = []
            orders = self.env['sale.order'].browse(
                [order_id for order_id in orders_by_partner[partner.id] if order_id]
            ).sorted('name')
            order_keys = [order.id for order in orders]
            if None in orders_by_partner[partner.id]:
                order_keys.append(None)

            for order_id in order_keys:
                values = results[(partner.id, order_id)]
                for group_key, group_values in values.items():
                    for label, value in group_values.items():
                        partner_values[group_key][label] += value
                        totals[group_key][label] += value
                order_lines.append(self._get_order_line(report, options, partner, order_id, values))

            lines.append((0, self._get_partner_line(report, options, partner, partner_values)))
            lines += [(0, line) for line in order_lines]

        if lines:
            lines.append((0, self._get_total_line(report, options, totals)))
        return lines

    def _query_values(self, report, options):
        """Return {(partner_id, order_id): {column_group_key: {expression_label: amount}}}.

        Invoiced amounts and retention follow the sales order of the invoice, the
        recovered advance follows the sales order of the advance payment so that the
        advance balance of each order adds up.
        """
        queries = []
        for column_group_key, column_group_options in report._split_options_per_column_group(options).items():
            query = report._get_report_query(column_group_options, 'from_beginning')
            queries.append(SQL(
                """
                (WITH contract_lines AS (
                    SELECT
                        partner.commercial_partner_id AS partner_id,
                        account_move_line.contract_order_id AS order_id,
                        recovered_from.contract_order_id AS advance_order_id,
                        account_move_line.contract_line_type,
                        account_move_line.display_type,
                        contract_move.move_type,
                        contract_account.account_type,
                        COALESCE(advance.is_contract_advance, FALSE) AS is_advance_payment,
                        %(balance)s AS balance
                    FROM %(table_references)s
                    %(currency_table_join)s
                    JOIN account_move contract_move ON contract_move.id = account_move_line.move_id
                    JOIN account_account contract_account ON contract_account.id = account_move_line.account_id
                    JOIN res_partner partner ON partner.id = account_move_line.partner_id
                    LEFT JOIN account_payment advance ON advance.id = account_move_line.payment_id
                    LEFT JOIN account_payment recovered_from ON recovered_from.id = contract_move.advance_payment_id
                    WHERE %(search_condition)s
                ),
                measures AS (
                    SELECT
                        partner_id,
                        order_id,
                        CASE WHEN move_type IN ('out_invoice', 'out_refund') AND display_type = 'payment_term'
                            THEN balance ELSE 0 END AS invoiced,
                        CASE WHEN contract_line_type = 'retention' THEN balance ELSE 0 END AS retention_held,
                        CASE WHEN contract_line_type = 'release' THEN -balance ELSE 0 END AS retention_released,
                        CASE WHEN is_advance_payment AND account_type = 'asset_receivable'
                            THEN -balance ELSE 0 END AS advance_received,
                        0 AS advance_recovered,
                        (contract_line_type IS NOT NULL OR is_advance_payment) AS is_contract
                    FROM contract_lines
                    UNION ALL
                    SELECT partner_id, advance_order_id, 0, 0, 0, 0, balance, TRUE
                    FROM contract_lines
                    WHERE contract_line_type = 'advance'
                )
                SELECT
                    %(column_group_key)s AS column_group_key,
                    partner_id,
                    order_id,
                    SUM(invoiced) AS invoiced,
                    SUM(retention_held) AS retention_held,
                    SUM(retention_released) AS retention_released,
                    SUM(advance_received) AS advance_received,
                    SUM(advance_recovered) AS advance_recovered
                FROM measures
                GROUP BY partner_id, order_id
                HAVING BOOL_OR(is_contract))
                """,
                column_group_key=column_group_key,
                balance=report._currency_table_apply_rate(SQL("account_move_line.balance")),
                table_references=query.from_clause,
                currency_table_join=report._currency_table_aml_join(column_group_options),
                search_condition=query.where_clause,
            ))

        results = defaultdict(lambda: defaultdict(lambda: dict.fromkeys(self._COLUMNS, 0.0)))
        for row in self.env.execute_query_dict(SQL(" UNION ALL ").join(queries)):
            values = results[(row['partner_id'], row['order_id'])][row['column_group_key']]
            for label in ('invoiced', 'retention_held', 'retention_released', 'advance_received', 'advance_recovered'):
                values[label] += row[label] or 0.0
            values['retention_balance'] = values['retention_held'] - values['retention_released']
            values['advance_balance'] = values['advance_received'] - values['advance_recovered']
        return results

    def _build_columns(self, report, options, values):
        columns = []
        for column in options['columns']:
            value = values.get(column['column_group_key'], {}).get(column['expression_label'], 0.0)
            columns.append(report._build_column_dict(value, column, options=options))
        return columns

    def _get_partner_line(self, report, options, partner, values):
        return {
            'id': report._get_generic_line_id('res.partner', partner.id),
            'name': partner.display_name,
            'columns': self._build_columns(report, options, values),
            'level': 1,
        }

    def _get_order_line(self, report, options, partner, order_id, values):
        partner_line_id = report._get_generic_line_id('res.partner', partner.id)
        if order_id:
            line_id = report._get_generic_line_id('sale.order', order_id, parent_line_id=partner_line_id)
            name = self.env['sale.order'].browse(order_id).name
        else:
            line_id = report._get_generic_line_id(None, None, markup='no_order', parent_line_id=partner_line_id)
            name = self.env._("Without sales order")
        return {
            'id': line_id,
            'name': name,
            'columns': self._build_columns(report, options, values),
            'level': 3,
            'caret_options': 'sale.order' if order_id else None,
        }

    def _get_total_line(self, report, options, totals):
        return {
            'id': report._get_generic_line_id(None, None, markup='total'),
            'name': self.env._("Total"),
            'class': 'total',
            'level': 1,
            'columns': self._build_columns(report, options, totals),
        }

    def _caret_options_initializer(self):
        return {
            'sale.order': [
                {'name': self.env._("View Sales Order"), 'action': 'caret_option_open_record_form'},
            ],
        }
