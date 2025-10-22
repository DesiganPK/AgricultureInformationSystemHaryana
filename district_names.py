"""Canonical Haryana districts and spelling aliases. LGD codes are NOT filled in:
get them from https://lgdirectory.gov.in and put them in LGD_CODES (name -> code)."""
import re
DISTRICTS = ["Ambala","Bhiwani","Charkhi Dadri","Faridabad","Fatehabad","Gurugram","Hisar","Jhajjar",
 "Jind","Kaithal","Karnal","Kurukshetra","Mahendragarh","Nuh","Palwal","Panchkula","Panipat","Rewari",
 "Rohtak","Sirsa","Sonipat","Yamunanagar"]
ALIASES = {"gurgaon":"Gurugram","hissar":"Hisar","mewat":"Nuh","mohindergarh":"Mahendragarh",
 "mahendergarh":"Mahendragarh","narnaul":"Mahendragarh","punchkula":"Panchkula","jhajar":"Jhajjar",
 "yamuna nagar":"Yamunanagar","sonepat":"Sonipat","sonipat":"Sonipat","kurukshetra":"Kurukshetra",
 "dadri":"Charkhi Dadri","charkhi dadri":"Charkhi Dadri","mewat (nuh)":"Nuh","rewari":"Rewari","rewary":"Rewari"}
LGD_CODES = {}   # fill in: {"Hisar": <code>, ...}
def _norm(s): return re.sub(r"[^a-z ]","",str(s).lower().replace("-"," ")).strip()
_LOOKUP = {_norm(d): d for d in DISTRICTS}; _LOOKUP.update({_norm(k): v for k, v in ALIASES.items()})
def canonical(raw):
    """Return canonical name or None if the spelling is not recognised."""
    return _LOOKUP.get(_norm(raw))
