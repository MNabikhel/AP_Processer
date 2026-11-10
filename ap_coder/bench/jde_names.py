"""The made-up company's lists (``jde_company`` builds its books from them): the rows of its chart of accounts, what
its trade suppliers sell, and the names its suppliers, people, departments and offices are made of. Every number,
description and name here is invented: none comes from a real company's books."""

from __future__ import annotations

# object, subsidiary, level of detail, posting edit code ("-": none), description | the remarks' words.
# Classes (the first digit): 0 not G/L, 1 assets, 2 liabilities and equity, 3 revenue, 4 direct costs, 7 general
# and administrative overhead, 8 other income and expense, 9 income taxes.
CHART = """
0012 - 5 N Statistical Units - Not G/L
0030 - 6 N Forecast Hours
1001 - 3 N Asset Accounts - Total
1010 - 4 N Current Asset Accounts
1100 - 5 N Cash & Bank
1120 - 7 M Operating Bank Accounts
1120 CDOPS 8 M Bank - CAD Operating
1400 - 5 N Receivables - Other
1450 - 7 B Employee Advances
1600 - 5 N Prepaids & Deposits
1620 - 7 B Prepaid Costs
1620 110 8 - Prepaid Software Licences | prepaid licence, multi-year licence
1620 120 8 - Prepaid Insurance Premiums | prepaid premium, policy renewal
1620 130 8 - Prepaid Memberships | prepaid membership, annual membership
1620 190 8 L Deposit Refunds - CAD
2001 - 3 N Total Liabilities & Equity
2010 - 4 N Liabilities - Current
2100 - 5 N Payables
2110 - 7 M Trade Payables
2110 901 8 I A/P Conversion Adjustments
2110 905 8 - A/P Accruals - CAD
2140 - 5 N Due to Affiliates
2150 - 7 M Intercompany Payable
2150 010 7 - Interco Payable - General
2150 WR4KD2 8 - Prairie Region - Interco
2200 - 5 N Payroll Remittances Payable
2230 - 7 B Workers Compensation
2230 AB 9 - Workers Comp - AB | wcb premium, wcb instalment
2230 BC 9 - Workers Comp - BC | wcb premium bc, wcb bc instalment
2230 SK 9 - Workers Comp - SK | wcb premium sk
2240 - 7 B Health Premiums
2240 BC 8 - Employer Health Tax - BC | eht instalment
2240 MB 8 - Health & Education Levy - MB | he levy
2260 - 7 B Group Savings Plans
2260 010 7 - Group Retirement Plan - A | retirement plan remit
2260 020 7 - Group Savings Plan - A | savings plan remit
2270 - 7 B Pension Plan
2270 110 8 - Pension DC - Employee | pension remit ee
2270 120 8 - Pension DC - Employer | pension remit er
2270 130 8 - Pension - Leave Top-ups | pension leave top-up
2280 - 7 - Union Dues Withheld | union dues
2300 - 5 N Taxes Payable - Non Payroll
2310 - 7 - Sales Tax Payable - Canada
2400 - 5 N Clearing
2420 - 7 - GST/HST Input Clearing
2425 - 7 - PST Clearing
2480 - 7 N Clearing - Other
2480 310 8 - Clearing - Miscellaneous | to be allocated, misc clearing
2480 320 8 - Personal Charges Clearing | personal charge, personal on visa
2480 330 8 - Corporate Card Clearing | card statement, visa statement
3010 - 3 N Revenue - Total
4010 - 3 N Direct Costs - Total
7001 - 4 N General & Admin Overhead
7100 - 5 N Staff & Employee Costs
7120 - 7 M Recruiting & Onboarding
7120 2107 8 - Recruiting - Agency Fees | placement fee, recruiter fee
7120 2113 8 - Recruiting - Job Postings | job posting, careers ad
7120 2141 8 - Student & Co-op Programs | co-op lunch, student event
7140 - 7 M Training & Learning
7140 2207 8 - Courses & Seminars | course fee, seminar registration
7140 2213 8 - Professional Dues | annual dues, designation dues
7140 2231 8 - Leadership Programs | leadership workshop, coaching session
7160 - 7 B Employee Wellness & Events
7160 2307 8 - Staff Events | staff bbq, holiday party
7160 2313 8 - Wellness Program | wellness session, flu clinic
7160 2331 8 - Service Awards | service awards, recognition gifts
7160 2343 8 - Team Apparel | team jackets, branded shirts
7300 - 5 N Premises
7310 - 7 B Office Lease
7310 3107 8 - Lease Payments - Head Office | monthly lease, lease payment
7310 3113 8 - Lease Operating Costs | operating costs, op cost true-up
7310 3121 8 - Parking Stalls | parking stalls, visitor parking
7330 - 7 M Building Upkeep
7330 3207 8 - Repairs - General | door repair, plumbing repair, light fixtures
7330 3213 8 - HVAC Service | hvac service, filter change
7330 3227 8 - Alarm & Access Control | alarm monitoring, access cards
7330 3233 8 - Signage | door signs, lobby signage
7340 - 7 M Cleaning Services
7340 3307 8 - Janitorial Contract | monthly cleaning, janitorial
7340 3313 8 - Carpet & Window Cleaning | carpet cleaning, window cleaning
7340 3321 8 - Shredding & Recycling | shredding service, recycling pickup
7350 - 7 B Utilities
7350 3407 8 - Electricity | power bill
7350 3413 8 - Natural Gas | gas bill
7350 3421 8 - Water & Sewer | water bill
7360 - 7 M Property Insurance & Taxes
7360 3507 8 - Contents Insurance | contents insurance
7360 3513 8 - Municipal Property Tax | property tax
7400 - 5 N Office Costs
7410 - 7 M Office & Kitchen Supplies
7410 4107 8 - Stationery & Desk Supplies | stationery, pens & paper, desk supplies
7410 4113 8 - Coffee & Kitchen Supplies | coffee supplies, kitchen supplies, water cooler
7410 4121 8 - Printer Toner & Paper | toner, copy paper
7410 4133 8 - Small Furniture & Fixtures | desk chair, filing cabinet, monitor arm
7410 4141 8 - First Aid & Safety Supplies | first aid kit, safety supplies
7410 4190 8 I Typewriter Ribbons
7420 - 7 M Postage & Courier
7420 4207 8 - Courier - Domestic | courier charges, overnight envelope
7420 4213 8 - Courier - Cross Border | customs courier, intl courier
7420 4221 8 - Postage & Mailroom | postage refill, mailroom supplies
7430 - 7 B Printing & Copying
7430 4307 8 - Copier Lease & Usage | copier lease, copier usage
7430 4313 8 - Outside Printing | business cards, printed binders, banners
7440 - 7 M Subscriptions & Books
7440 4407 8 - News & Trade Journals | journal renewal, news subscription
7440 4413 8 - Reference Materials | code books, standards manual
7440 4421 8 - Online Research Tools | research database, online library
7450 - 7 M Records & Storage
7450 4507 8 - Offsite File Storage | box storage, file retrieval
7450 4513 8 - Archive Scanning | archive scanning, scanning project
7500 - 5 N Technology & Telecom
7510 - 7 B Telephone & Mobile
7510 5107 8 - Cell Phones & Plans | cell phones, mobile plan
7510 5113 8 - Desk Phone Lines | phone lines, fax line
7510 5121 8 - Conference Calling | conference bridge, webinar licence
7520 - 7 M Data & Internet
7520 5207 8 - Internet Circuits | fibre internet, internet circuit
7520 5213 8 - Site-to-Site Links | wan link, backup link
7520 5221 8 - Wi-Fi & Network Gear | wifi access points, network switch
7530 - 7 M Software & Licences
7530 5307 8 - Software Subscriptions | software subscription, licence renewal
7530 5313 8 - Software Maintenance | annual maintenance, support renewal
7530 5321 8 - Cloud Hosting | cloud hosting, hosting usage
7540 - 7 M Computer Equipment
7540 5407 8 - Laptops & Desktops | laptop, docking stations
7540 5413 8 - Peripherals | monitors, headsets, keyboards
7540 5421 8 - Equipment Repair | laptop repair, screen replacement
7550 - 7 M IT Services
7550 5507 8 - Managed Services | managed services, service desk hours
7550 5513 8 - IT Consulting | it consulting, project hours
7600 - 5 N Professional Fees
7610 - 7 M Legal Fees
7610 6107 8 - Legal - General Counsel | legal fees, general matters
7610 6113 8 - Legal - Employment | employment matter, hr legal advice
7620 - 7 M Audit & Tax Fees
7620 6207 8 - Audit Fees | audit progress billing, interim audit
7620 6213 8 - Tax Advisory | tax advisory, tax filing
7630 - 7 M Consulting Fees
7630 6307 8 - Management Consulting | consulting fees, strategy session
7630 6313 8 - Translation Services | translation, document translation
7630 6321 8 - Surveys & Research | staff survey, market research
7640 - 7 M Corporate Insurance
7640 6407 8 - Liability Insurance | liability premium
7640 6413 8 - Brokerage Fees | broker fee
7700 - 5 N Travel & Events
7710 - 7 M Travel
7710 7107 8 - Airfare | airfare, flight change
7710 7113 8 - Hotels & Lodging | hotel, lodging
7710 7121 8 - Ground Transport | taxi, car rental, mileage
7710 7133 8 - Meals - Travel | travel meals, per diem
7720 - 7 M Meals & Hospitality
7720 7207 8 - Working Lunches | team lunch, working lunch, lunch meeting
7720 7213 8 - Client Hospitality | client dinner, hospitality
7730 - 7 M Meetings & Conferences
7730 7307 8 - Conference Fees | conference registration
7730 7313 8 - Meeting Room Rental | meeting room, av rental
7730 7321 8 - Catering - Meetings | catering, breakfast order
7730 7333 8 - Team Offsites | offsite day, planning retreat
7800 - 5 N Community & Other
7810 - 7 M Donations & Sponsorships
7810 8107 8 - Charitable Donations | donation, charity run
7810 8113 8 - Community Sponsorships | sponsorship, team sponsorship
7810 8121 8 - Scholarships | scholarship award
7820 - 7 M Bank & Card Fees
7820 8207 8 - Bank Service Charges | bank charges, wire fee
7820 8213 8 - Card Merchant Fees | merchant fees
7830 - 7 M Vehicle Costs
7830 8307 8 - Fleet Fuel | fuel, fuel card
7830 8313 8 - Vehicle Maintenance | oil change, tires
8010 - 3 N Other Income & Expense
9010 - 3 N Income Taxes - Total
"""

# What a trade supplier sells: the words its name ends with | the objects AP codes it to.
TRADES = """
OFFICE PRODUCTS, STATIONERS, OFFICE SUPPLY | 7410
COFFEE SERVICES, WATER & COFFEE | 7410
COURIER, EXPRESS DELIVERY, MESSENGER SERVICE | 7420
PRINT SHOP, GRAPHICS, COPY CENTRE | 7430 7440
TELECOM, WIRELESS, COMMUNICATIONS | 7510 7520
SOFTWARE, DATA SYSTEMS, DIGITAL | 7530 1620
COMPUTER SOLUTIONS, TECH SUPPLY | 7540 7550 7520
LAW OFFICE, LEGAL COUNSEL | 7610
CHARTERED ACCOUNTANTS, TAX ADVISORS | 7620
CONSULTING, ADVISORY GROUP | 7630 7140
PROPERTY MANAGEMENT, REALTY | 7310 7330 7350 7360
JANITORIAL, BUILDING SERVICES, MECHANICAL | 7330 7340
POWER & GAS, UTILITIES | 7350
CATERING, BISTRO, BAKERY & CAFE | 7720 7730 7160
HOTEL, INN & SUITES, TRAVEL | 7710 7730
STAFFING, TALENT SEARCH, TRAINING INSTITUTE | 7120 7140
RECORDS STORAGE, SHREDDING | 7450 7340
INSURANCE BROKERS | 7640 7360 1620
FOUNDATION, COMMUNITY SOCIETY | 7810
AUTO SERVICE, FUEL STOP | 7830
MOVING & STORAGE, VAN LINES, FURNISHED SUITES | 1450
"""
PREFIXES = (
    "ALDERBROOK, AMBERLEY, ASPEN RIDGE, BIRCHWAY, BLUE HERON, BRACKEN, CEDAR HOLLOW, COPPER KETTLE, CROWFOOT, "
    "DRIFTWOOD, EASTGATE, ELKHORN, FERNDALE, FOXGLOVE, GREYSTONE, HAWTHORN, HEARTHSIDE, IRONWOOD, JUNIPER LANE, "
    "KESTREL, LARKSPUR, LONGVIEW, MILLRACE, MOSSBANK, NINE MILE, OAK & EMBER, OTTER CREEK, PINECREST, QUILL & CO, "
    "REDFERN, ROSEHILL, SADDLEBACK, SILVERTIP, STONEBRIDGE, SUMMIT TRAIL, TAMARACK, THREE RIVERS, UPLAND, "
    "VIOLET HILL, WESTMEADOW, WILLOWBANK, WINDROW, YELLOWPOINT, PRAIRIE SKY, HIGH LEVEL, CLEARWATER, BEACON HILL, "
    "GOLDFINCH, MARLOWE, TIDEWATER"
).split(", ")
LAST_NAMES = (
    "ACHESON, ARCAND, BAPTISTE, BELLEROSE, BOUCHER, CARDINAL, CHIASSON, DHALIWAL, DUECK, EASTMAN, FEHR, FONTAINE, "
    "GAUDET, GREWAL, HIEBERT, IWASAKI, JANVIER, KOWALCHUK, LAFRAMBOISE, LOEWEN, MACISAAC, MERCREDI, NAKAMURA, "
    "NGUYEN, OKAFOR, OLIJNYK, PELLETIER, PENNER, QUESNEL, RAMOTAR, SANDHU, SEMENIUK, TKACHUK, TOEWS, UNGER, "
    "VANDERMEER, WASYLENKO, WIEBE, YEE, ZACHARIAS"
).split(", ")
FIRST_NAMES = (
    "ALICIA, BRENT, CARMEN, DARCY, ELISE, FARID, GWEN, HARJIT, IRENE, JOEL, KAREN, LIAM, MEI, NADIA, OWEN, PRIYA, "
    "QUINN, RENEE, SUNIL, TANYA, URSULA, VICTOR, WANDA, XAVIER, YVONNE, ZANE, COLETTE, DEVON, ESTHER, GRANT"
).split(", ")
AFFILIATES = (
    ("MAPLE RIDGE SHARED SERVICES INC.", "CAD", 0.40),
    ("MAPLE RIDGE HOLDINGS USA, INC.", "USD", 0.30),
    ("MAPLE RIDGE HOLDINGS (PRAIRIES) LTD.", "CAD", 0.15),
    ("MAPLE RIDGE PROPERTIES LTD.", "CAD", 0.10),
    ("MAPLE RIDGE EQUIPMENT LEASING LTD.", "CAD", 0.05),
)
# Payroll-type remittances: the name (the rest of a long one goes to Long Address) and the liabilities it is paid from.
REMITTANCES = (
    ("CASCADE MUTUAL LIFE ASSURANCE COMPANY", "", "2270 2260"),
    ("TRADES & TECHNICAL PENSION TRUST OF THE", "PRAIRIE PROVINCES", "2270"),
    ("WORKERS SAFETY BOARD - PRAIRIES", "", "2230"),
    ("PROVINCIAL HEALTH PREMIUM OFFICE", "", "2240"),
    ("LOCAL 512 BENEFIT TRUST", "", "2280"),
    ("WORKERS SAFETY BOARD - COASTAL", "", "2230"),
    ("SUMMIT GROUP SAVINGS ADMINISTRATORS", "", "2260"),
    ("LOCAL 88 DUES OFFICE", "", "2280"),
)
DEPARTMENTS = (
    "Finance & Accounting, People & Culture, Payroll Services, Procurement, Legal & Contracts, Health & Safety, "
    "Communications, Leadership Office, Facilities, Risk & Insurance, Internal Audit, Treasury, Tax, "
    "Corporate Growth, Sustainability, Quality, Learning Centre, Records Management, Travel Desk, "
    "Estimating Support, Fleet Administration, Marketing, Reception & Mail"
).split(", ")
REGIONS = ("Prairie", "Coastal", "Northern", "Lakehead", "Atlantic", "Capital", "Foothills", "Interior")
IT_UNITS = (
    "Data Centre, Network Services, Service Desk, Business Systems, Information Security, Collaboration Tools, "
    "Reporting & Analytics, Field Devices, Print Services, ERP Support, Web & Intranet, Telecom"
).split(", ")
STREETS = ("Alder", "Quarry Rd", "104 st", "Lakeshore", "51 ave", "Dunmore", "Ferry Rd", "8 st NE")
GENERIC_REMARKS = ("monthly invoice", "as per quote", "see attached", "statement", "misc purchase", "invoice")
USERS = ("LTREMBLAY", "MOKAFOR", "SPATEL2")
