"""IBAN registry data per ISO 13616 (generated, do not edit by hand).

Source: SWIFT IBAN Registry, Release 103 - Sep 2026 (TXT release of the registry, data
elements "IBAN structure", "IBAN length", "IBAN electronic format example").
Registry page: https://www.swift.com/standards/data-standards/iban-international-bank-account-number
Checked 2026-10-23; module generated 2026-10-23 by dist/refdata/gen_iban.py.

Registry notation: N!n = N digits, N!a = N upper-case letters, N!c = N letters
or digits. ``IBAN_REGISTRY`` maps the two-letter IBAN prefix to its total
length and a full-match regex built from the structure. ``IBAN_EXAMPLES`` holds
the official electronic-format examples (used by tests).
"""

import re
from dataclasses import dataclass
from typing import Final

IBAN_REGISTRY_RELEASE: Final[str] = "Release 103 - Sep 2026"


@dataclass(frozen=True, slots=True)
class IbanSpec:
    """Length and structure of the IBAN for one registry entry."""

    country: str
    length: int
    structure: str
    pattern: re.Pattern[str]


_RAW: Final[tuple[tuple[str, int, str, str], ...]] = (
    ("AD", 24, "AD2!n4!n4!n12!c", r"AD\d{2}\d{4}\d{4}[A-Z0-9]{12}"),  # Andorra
    ("AE", 23, "AE2!n3!n16!n", r"AE\d{2}\d{3}\d{16}"),  # United Arab Emirates (The)
    ("AL", 28, "AL2!n8!n16!c", r"AL\d{2}\d{8}[A-Z0-9]{16}"),  # Albania
    ("AT", 20, "AT2!n5!n11!n", r"AT\d{2}\d{5}\d{11}"),  # Austria
    ("AZ", 28, "AZ2!n4!a20!c", r"AZ\d{2}[A-Z]{4}[A-Z0-9]{20}"),  # Azerbaijan
    ("BA", 20, "BA2!n3!n3!n8!n2!n", r"BA\d{2}\d{3}\d{3}\d{8}\d{2}"),  # Bosnia and Herzegovina
    ("BE", 16, "BE2!n3!n7!n2!n", r"BE\d{2}\d{3}\d{7}\d{2}"),  # Belgium
    ("BG", 22, "BG2!n4!a4!n2!n8!c", r"BG\d{2}[A-Z]{4}\d{4}\d{2}[A-Z0-9]{8}"),  # Bulgaria
    ("BH", 22, "BH2!n4!a14!c", r"BH\d{2}[A-Z]{4}[A-Z0-9]{14}"),  # Bahrain
    ("BI", 27, "BI2!n5!n5!n11!n2!n", r"BI\d{2}\d{5}\d{5}\d{11}\d{2}"),  # Burundi
    (
        "BR",
        29,
        "BR2!n8!c5!n10!n1!c1!c",
        r"BR\d{2}[A-Z0-9]{8}\d{5}\d{10}[A-Z0-9]{1}[A-Z0-9]{1}",
    ),  # Brazil
    ("BY", 28, "BY2!n4!c4!n16!c", r"BY\d{2}[A-Z0-9]{4}\d{4}[A-Z0-9]{16}"),  # Belarus
    ("CH", 21, "CH2!n5!n12!c", r"CH\d{2}\d{5}[A-Z0-9]{12}"),  # Switzerland
    ("CR", 22, "CR2!n4!n14!n", r"CR\d{2}\d{4}\d{14}"),  # Costa Rica
    ("CY", 28, "CY2!n3!n5!n16!c", r"CY\d{2}\d{3}\d{5}[A-Z0-9]{16}"),  # Cyprus
    ("CZ", 24, "CZ2!n4!n6!n10!n", r"CZ\d{2}\d{4}\d{6}\d{10}"),  # Czechia
    ("DE", 22, "DE2!n8!n10!n", r"DE\d{2}\d{8}\d{10}"),  # Germany
    ("DJ", 27, "DJ2!n5!n5!n11!n2!n", r"DJ\d{2}\d{5}\d{5}\d{11}\d{2}"),  # Djibouti
    ("DK", 18, "DK2!n4!n9!n1!n", r"DK\d{2}\d{4}\d{9}\d{1}"),  # Denmark
    ("DO", 28, "DO2!n4!c20!n", r"DO\d{2}[A-Z0-9]{4}\d{20}"),  # Dominican Republic
    ("EE", 20, "EE2!n2!n14!n", r"EE\d{2}\d{2}\d{14}"),  # Estonia
    ("EG", 29, "EG2!n4!n4!n17!n", r"EG\d{2}\d{4}\d{4}\d{17}"),  # Egypt
    ("ES", 24, "ES2!n4!n4!n1!n1!n10!n", r"ES\d{2}\d{4}\d{4}\d{1}\d{1}\d{10}"),  # Spain
    ("FI", 18, "FI2!n3!n11!n", r"FI\d{2}\d{3}\d{11}"),  # Finland
    ("FK", 18, "FK2!n2!a12!n", r"FK\d{2}[A-Z]{2}\d{12}"),  # Falkland Islands (Malvinas)
    ("FO", 18, "FO2!n4!n9!n1!n", r"FO\d{2}\d{4}\d{9}\d{1}"),  # Faroe Islands
    ("FR", 27, "FR2!n5!n5!n11!c2!n", r"FR\d{2}\d{5}\d{5}[A-Z0-9]{11}\d{2}"),  # France
    ("GB", 22, "GB2!n4!a6!n8!n", r"GB\d{2}[A-Z]{4}\d{6}\d{8}"),  # United Kingdom
    ("GE", 22, "GE2!n2!a16!n", r"GE\d{2}[A-Z]{2}\d{16}"),  # Georgia
    ("GI", 23, "GI2!n4!a15!c", r"GI\d{2}[A-Z]{4}[A-Z0-9]{15}"),  # Gibraltar
    ("GL", 18, "GL2!n4!n9!n1!n", r"GL\d{2}\d{4}\d{9}\d{1}"),  # Greenland
    ("GR", 27, "GR2!n3!n4!n16!c", r"GR\d{2}\d{3}\d{4}[A-Z0-9]{16}"),  # Greece
    ("GT", 28, "GT2!n4!c20!c", r"GT\d{2}[A-Z0-9]{4}[A-Z0-9]{20}"),  # Guatemala
    ("HN", 28, "HN2!n4!a20!n", r"HN\d{2}[A-Z]{4}\d{20}"),  # Honduras
    ("HR", 21, "HR2!n7!n10!n", r"HR\d{2}\d{7}\d{10}"),  # Croatia
    ("HU", 28, "HU2!n3!n4!n1!n15!n1!n", r"HU\d{2}\d{3}\d{4}\d{1}\d{15}\d{1}"),  # Hungary
    ("IE", 22, "IE2!n4!a6!n8!n", r"IE\d{2}[A-Z]{4}\d{6}\d{8}"),  # Ireland
    ("IL", 23, "IL2!n3!n3!n13!n", r"IL\d{2}\d{3}\d{3}\d{13}"),  # Israel
    ("IQ", 23, "IQ2!n4!a3!n12!n", r"IQ\d{2}[A-Z]{4}\d{3}\d{12}"),  # Iraq
    ("IS", 26, "IS2!n4!n2!n6!n10!n", r"IS\d{2}\d{4}\d{2}\d{6}\d{10}"),  # Iceland
    ("IT", 27, "IT2!n1!a5!n5!n12!c", r"IT\d{2}[A-Z]{1}\d{5}\d{5}[A-Z0-9]{12}"),  # Italy
    ("JO", 30, "JO2!n4!a4!n18!c", r"JO\d{2}[A-Z]{4}\d{4}[A-Z0-9]{18}"),  # Jordan
    ("KW", 30, "KW2!n4!a22!c", r"KW\d{2}[A-Z]{4}[A-Z0-9]{22}"),  # Kuwait
    ("KZ", 20, "KZ2!n3!n13!c", r"KZ\d{2}\d{3}[A-Z0-9]{13}"),  # Kazakhstan
    ("LB", 28, "LB2!n4!n20!c", r"LB\d{2}\d{4}[A-Z0-9]{20}"),  # Lebanon
    ("LC", 32, "LC2!n4!a24!c", r"LC\d{2}[A-Z]{4}[A-Z0-9]{24}"),  # Saint Lucia
    ("LI", 21, "LI2!n5!n12!c", r"LI\d{2}\d{5}[A-Z0-9]{12}"),  # Liechtenstein
    ("LT", 20, "LT2!n5!n11!n", r"LT\d{2}\d{5}\d{11}"),  # Lithuania
    ("LU", 20, "LU2!n3!n13!c", r"LU\d{2}\d{3}[A-Z0-9]{13}"),  # Luxembourg
    ("LV", 21, "LV2!n4!a13!c", r"LV\d{2}[A-Z]{4}[A-Z0-9]{13}"),  # Latvia
    ("LY", 25, "LY2!n3!n3!n15!n", r"LY\d{2}\d{3}\d{3}\d{15}"),  # Libya
    ("MC", 27, "MC2!n5!n5!n11!c2!n", r"MC\d{2}\d{5}\d{5}[A-Z0-9]{11}\d{2}"),  # Monaco
    ("MD", 24, "MD2!n2!c18!c", r"MD\d{2}[A-Z0-9]{2}[A-Z0-9]{18}"),  # "Moldova, Republic of"
    ("ME", 22, "ME2!n3!n13!n2!n", r"ME\d{2}\d{3}\d{13}\d{2}"),  # Montenegro
    ("MK", 19, "MK2!n3!n10!c2!n", r"MK\d{2}\d{3}[A-Z0-9]{10}\d{2}"),  # North Macedonia
    ("MN", 20, "MN2!n4!n12!n", r"MN\d{2}\d{4}\d{12}"),  # Mongolia
    ("MR", 27, "MR2!n5!n5!n11!n2!n", r"MR\d{2}\d{5}\d{5}\d{11}\d{2}"),  # Mauritania
    ("MT", 31, "MT2!n4!a5!n18!c", r"MT\d{2}[A-Z]{4}\d{5}[A-Z0-9]{18}"),  # Malta
    (
        "MU",
        30,
        "MU2!n4!a2!n2!n12!n3!n3!a",
        r"MU\d{2}[A-Z]{4}\d{2}\d{2}\d{12}\d{3}[A-Z]{3}",
    ),  # Mauritius
    ("NI", 28, "NI2!n4!a20!n", r"NI\d{2}[A-Z]{4}\d{20}"),  # Nicaragua
    ("NL", 18, "NL2!n4!a10!n", r"NL\d{2}[A-Z]{4}\d{10}"),  # Netherlands (The)
    ("NO", 15, "NO2!n4!n6!n1!n", r"NO\d{2}\d{4}\d{6}\d{1}"),  # Norway
    ("OM", 23, "OM2!n3!n16!c", r"OM\d{2}\d{3}[A-Z0-9]{16}"),  # Oman
    ("PK", 24, "PK2!n4!a16!c", r"PK\d{2}[A-Z]{4}[A-Z0-9]{16}"),  # Pakistan
    ("PL", 28, "PL2!n8!n16!n", r"PL\d{2}\d{8}\d{16}"),  # Poland
    ("PS", 29, "PS2!n4!a21!c", r"PS\d{2}[A-Z]{4}[A-Z0-9]{21}"),  # "Palestine, State of"
    ("PT", 25, "PT2!n4!n4!n11!n2!n", r"PT\d{2}\d{4}\d{4}\d{11}\d{2}"),  # Portugal
    ("QA", 29, "QA2!n4!a21!c", r"QA\d{2}[A-Z]{4}[A-Z0-9]{21}"),  # Qatar
    ("RO", 24, "RO2!n4!a16!c", r"RO\d{2}[A-Z]{4}[A-Z0-9]{16}"),  # Romania
    ("RS", 22, "RS2!n3!n13!n2!n", r"RS\d{2}\d{3}\d{13}\d{2}"),  # Serbia
    ("RU", 33, "RU2!n9!n5!n15!c", r"RU\d{2}\d{9}\d{5}[A-Z0-9]{15}"),  # Russian Federation
    ("SA", 24, "SA2!n2!n18!c", r"SA\d{2}\d{2}[A-Z0-9]{18}"),  # Saudi Arabia
    ("SC", 31, "SC2!n4!a2!n2!n16!n3!a", r"SC\d{2}[A-Z]{4}\d{2}\d{2}\d{16}[A-Z]{3}"),  # Seychelles
    ("SD", 18, "SD2!n2!n12!n", r"SD\d{2}\d{2}\d{12}"),  # Sudan
    ("SE", 24, "SE2!n3!n16!n1!n", r"SE\d{2}\d{3}\d{16}\d{1}"),  # Sweden
    ("SI", 19, "SI2!n5!n8!n2!n", r"SI\d{2}\d{5}\d{8}\d{2}"),  # Slovenia
    ("SK", 24, "SK2!n4!n6!n10!n", r"SK\d{2}\d{4}\d{6}\d{10}"),  # Slovakia
    ("SM", 27, "SM2!n1!a5!n5!n12!c", r"SM\d{2}[A-Z]{1}\d{5}\d{5}[A-Z0-9]{12}"),  # San Marino
    ("SO", 23, "SO2!n4!n3!n12!n", r"SO\d{2}\d{4}\d{3}\d{12}"),  # Somalia
    ("ST", 25, "ST2!n4!n4!n11!n2!n", r"ST\d{2}\d{4}\d{4}\d{11}\d{2}"),  # Sao Tome and Principe
    ("SV", 28, "SV2!n4!a20!n", r"SV\d{2}[A-Z]{4}\d{20}"),  # El Salvador
    ("TL", 23, "TL2!n3!n14!n2!n", r"TL\d{2}\d{3}\d{14}\d{2}"),  # Timor-Leste
    ("TN", 24, "TN2!n2!n3!n13!n2!n", r"TN\d{2}\d{2}\d{3}\d{13}\d{2}"),  # Tunisia
    ("TR", 26, "TR2!n5!n1!n16!c", r"TR\d{2}\d{5}\d{1}[A-Z0-9]{16}"),  # Turkiye
    ("UA", 29, "UA2!n6!n19!c", r"UA\d{2}\d{6}[A-Z0-9]{19}"),  # Ukraine
    ("VA", 22, "VA2!n3!n15!n", r"VA\d{2}\d{3}\d{15}"),  # Holy See
    ("VG", 24, "VG2!n4!a16!n", r"VG\d{2}[A-Z]{4}\d{16}"),  # Virgin Islands (British)
    ("XK", 20, "XK2!n4!n10!n2!n", r"XK\d{2}\d{4}\d{10}\d{2}"),  # Kosovo
    ("YE", 30, "YE2!n4!a4!n18!c", r"YE\d{2}[A-Z]{4}\d{4}[A-Z0-9]{18}"),  # Yemen
)

#: IBAN prefix -> IbanSpec, for every country in the registry.
IBAN_REGISTRY: Final[dict[str, IbanSpec]] = {
    code: IbanSpec(code, length, structure, re.compile(f"^{regex}$"))
    for code, length, structure, regex in _RAW
}

#: IBAN prefix -> official "IBAN electronic format example".
IBAN_EXAMPLES: Final[dict[str, str]] = {
    "AD": "AD1200012030200359100100",
    "AE": "AE070331234567890123456",
    "AL": "AL47212110090000000235698741",
    "AT": "AT611904300234573201",
    "AZ": "AZ21NABZ00000000137010001944",
    "BA": "BA391290079401028494",
    "BE": "BE68539007547034",
    "BG": "BG80BNBG96611020345678",
    "BH": "BH67BMAG00001299123456",
    "BI": "BI4210000100010000332045181",
    "BR": "BR6699999A03000010009795493C1",
    "BY": "BY13NBRB3600900000002Z00AB00",
    "CH": "CH9300762011623852957",
    "CR": "CR05015202001026284066",
    "CY": "CY17002001280000001200527600",
    "CZ": "CZ6508000000192000145399",
    "DE": "DE89370400440532013000",
    "DJ": "DJ2100010000000154000100186",
    "DK": "DK5000400440116243",
    "DO": "DO28BAGR00000001212453611324",
    "EE": "EE382200221020145685",
    "EG": "EG380019000500000000263180002",
    "ES": "ES9121000418450200051332",
    "FI": "FI2112345600000785",
    "FK": "FK88SC123456789012",
    "FO": "FO6264600001631634",
    "FR": "FR1420041010050500013M02606",
    "GB": "GB29NWBK60161331926819",
    "GE": "GE29NB0000000101904917",
    "GI": "GI75NWBK000000007099453",
    "GL": "GL8964710001000206",
    "GR": "GR1601101250000000012300695",
    "GT": "GT82TRAJ01020000001210029690",
    "HN": "HN88CABF00000000000250005469",
    "HR": "HR1210010051863000160",
    "HU": "HU42117730161111101800000000",
    "IE": "IE29AIBK93115212345678",
    "IL": "IL620108000000099999999",
    "IQ": "IQ98NBIQ850123456789012",
    "IS": "IS140159260076545510730339",
    "IT": "IT60X0542811101000000123456",
    "JO": "JO94CBJO0010000000000131000302",
    "KW": "KW81CBKU0000000000001234560101",
    "KZ": "KZ86125KZT5004100100",
    "LB": "LB62099900000001001901229114",
    "LC": "LC55HEMM000100010012001200023015",
    "LI": "LI21088100002324013AA",
    "LT": "LT121000011101001000",
    "LU": "LU280019400644750000",
    "LV": "LV80BANK0000435195001",
    "LY": "LY83002048000020100120361",
    "MC": "MC5811222000010123456789030",
    "MD": "MD24AG000225100013104168",
    "ME": "ME25505000012345678951",
    "MK": "MK07250120000058984",
    "MN": "MN121234123456789123",
    "MR": "MR1300020001010000123456753",
    "MT": "MT84MALT011000012345MTLCAST001S",
    "MU": "MU17BOMM0101101030300200000MUR",
    "NI": "NI45BAPR00000013000003558124",
    "NL": "NL91ABNA0417164300",
    "NO": "NO9386011117947",
    "OM": "OM810180000001299123456",
    "PK": "PK36SCBL0000001123456702",
    "PL": "PL61109010140000071219812874",
    "PS": "PS92PALS000000000400123456702",
    "PT": "PT50000201231234567890154",
    "QA": "QA58DOHB00001234567890ABCDEFG",
    "RO": "RO49AAAA1B31007593840000",
    "RS": "RS35260005601001611379",
    "RU": "RU0304452522540817810538091310419",
    "SA": "SA0380000000608010167519",
    "SC": "SC18SSCB11010000000000001497USD",
    "SD": "SD2129010501234001",
    "SE": "SE4550000000058398257466",
    "SI": "SI56263300012039086",
    "SK": "SK3112000000198742637541",
    "SM": "SM86U0322509800000000270100",
    "SO": "SO211000001001000100141",
    "ST": "ST23000100010051845310146",
    "SV": "SV62CENR00000000000000700025",
    "TL": "TL380080012345678910157",
    "TN": "TN5910006035183598478831",
    "TR": "TR330006100519786457841326",
    "UA": "UA213223130000026007233566001",
    "VA": "VA59001123000012345678",
    "VG": "VG96VPVG0000012345678901",
    "XK": "XK051212012345678906",
    "YE": "YE15CBYE0001018861234567891234",
}
