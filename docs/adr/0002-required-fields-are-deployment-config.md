# Required fields are deployment configuration, not schema

The document type defines which fields exist. Which of them must be present before a document can be auto-approved is set per deployment (`REQUIRED_FIELDS_INVOICE`, `REQUIRED_FIELDS_CUSTOMS_FORM`), and defaults to every field when unset. The schema was built against EU invoices, where an IBAN and a VAT ID are always printed. On 25 real US invoices from DocILE, `iban` was absent from all 25, so under a schema-fixed required set every document went to a human for a field it was never going to carry.

## Consequences

Loosening the required set is what lets a deployment auto-approve at all, and it is also what exposed wrong values being written unattended: on DocILE, 6 of 14 auto-approvals were fully correct. The default stays strict. A field name that is not in the schema stops the API at startup, so a typo cannot silently drop a field out of the required set.
