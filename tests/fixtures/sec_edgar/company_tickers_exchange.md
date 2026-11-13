# Security-listings fixture

`company_tickers_exchange.json` contains 20 real rows from a historical
copy of SEC's `https://www.sec.gov/files/company_tickers_exchange.json`.
It is a trimmed test fixture, **not** a complete or current SEC response.
Company names, CIKs, ticker spelling and exchanges are copied unchanged.
It includes Berkshire's `BRK-B` and a nullable exchange (Aircastle `AYR`).

The historical copy is pinned at:
https://github.com/larsondg2000/SEC-10K-and-10Q-Reports/blob/d653ee1d19c4703a311994ad2c01c390bb509450/company_tickers_exchange.json

- Full mirror file SHA-256:
  `e249c98cb878156389ce8e728b11352f54e3629798a2484a0ec4267ff823a915`
- Trimmed fixture SHA-256:
  `f9cd3364257b5f9fed1cd702236b2f0a8f580fe9493650da5c7cd16346696faa`

The mirror is a fixture source, not a runtime fallback or independent
verification of today's SEC directory. Tests read only committed bytes;
they never download this file. Archival tests compare the saved response
byte for byte with the fixture, including whitespace and its final newline.
