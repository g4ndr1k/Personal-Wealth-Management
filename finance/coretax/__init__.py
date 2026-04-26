"""CoreTax SPT — persistent tax-version ledger + reconciliation workflow.

Replaces the one-shot generator (coretax_export.py) with a multi-stage
wizard: import prior-year XLSX → carry-forward review → reconcile from
PWM → manual mapping → export XLSX.
"""
