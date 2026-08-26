# Phase 1C Rule Coverage

This matrix maps the installed `customer-invoice-price-check` Production Skill v2.0.1
and the approved Phase 1C overrides to deterministic backend behavior.

| IDs | Business rule | Phase 1C status | Test coverage |
|---|---|---|---|
| R01-R03 | Explicit customer priority, exact normalized customer, ambiguity stop | Implemented | Automated |
| R04-R07 | Invoice-date Active, EffectiveFrom/To inclusive eligibility | Implemented | Automated |
| R08-R10 | Exact first, one Exact, multiple Exact, Exact stops Prefix | Implemented | Automated |
| R11-R15 | Prefix end/separators, alphanumeric boundary rejection, exclusions | Implemented | Automated |
| R16 | Multiple eligible Prefix candidates | Implemented | Automated |
| R17 | No matching SKU rule | Implemented | Automated |
| R18-R20 | Notes non-empty, Price blank, Price zero pre-gates | Implemented | Automated |
| R21-R25 | Structured NoDiscount, HasDiscount, DiscountValue and conflicts | Implemented | Automated |
| R26-R28 | Regular/non-Carton, Carton and unchanged Carton state | Implemented | Automated |
| R29-R31 | Decimal amount, quantity, final-only ROUND_HALF_UP | Implemented | Automated |
| R32 | Preserve PRICE/DISC/AMOUNT for Manual Review | Implemented | Automated |
| R33 | Preserve every candidate rule and decision question | Implemented | Automated |
| R34 | Do not produce protected invoice-bottom-total decisions | Implemented | Automated |
| R35 | Source retrieval only through CustomerPriceProvider boundary | Implemented | Automated |
| D01-D10 | PDF text edits, highlights, colors, labels, bounding boxes, collision checks, atomic label groups, ITEM NO. readability, chat rendering and PDF output | Deferred to Phase 1D | Not applicable in Phase 1C |

Total mapped business rules: **45**  
Implemented: **35**  
Tested: **35**  
Deferred PDF/output-only rules: **10**  
Unmapped business rules: **0**
