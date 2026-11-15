{
    'name': "Contract Retention and Advance Recovery",
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': "Retention and advance recovery on progress invoices",
    'description': """
Retention and advance recovery for contracting companies
=========================================================

* Retention held on a dedicated receivable account with its own due date.
* Advance payments recovered from progress invoices and matched automatically.
* Release of retention to the normal receivable.
* Retention and advance report per customer and sales order.
""",
    'depends': ['sale', 'account_reports'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/account_payment_views.xml',
        'views/sale_order_views.xml',
    ],
    'license': 'OPL-1',
    'author': "Mruthul Raj",
    'installable': True,
}
