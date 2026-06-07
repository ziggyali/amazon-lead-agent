# Extract brand profile

You are extracting public information from a DTC brand website.

Return JSON with these fields only:

- company_name
- brand_name
- website
- category
- country
- description
- amazon_links
- amazon_evidence_summary
- amazon_backlink_found
- founder_or_executive_names
- ecommerce_or_marketplace_people
- public_emails
- contact_page_url
- decision_maker_source_url
- pain_points
- confidence
- source_quotes

Rules:

- Use only public, legally accessible information.
- Do not attempt to bypass login walls, CAPTCHAs, robots restrictions, anti-bot systems, or LinkedIn protections.
- If a field is unknown, return an empty string or empty list.
- Prefer concise values over verbose prose.

