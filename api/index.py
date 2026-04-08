"""
Single-file Vercel serverless handler.
All logic inlined — no local imports that could fail in Vercel's Python runtime.
"""
from __future__ import annotations
import logging
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=str(STATIC_DIR))
CORS(app)

_CACHE: dict[str, dict] = {}
_CACHE_TTL = 6 * 3600
MAX_WORKERS = 10

# ── Scoring engine ────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))

def _score_value(info: dict) -> tuple[float, dict]:
    pe = info.get("forwardPE") or info.get("trailingPE")
    pe_score = _clamp(1 - (pe - 5) / 45) * 10 if pe and pe > 0 else 0.0
    pb = info.get("priceToBook")
    pb_score = _clamp(1 - (pb - 0.5) / 5.5) * 8 if pb and pb > 0 else 0.0
    ps = info.get("priceToSalesTrailing12Months")
    ps_score = _clamp(1 - (ps - 0.5) / 6.5) * 7 if ps and ps > 0 else 0.0
    total = pe_score + pb_score + ps_score
    return total, {
        "total": round(total, 1),
        "pe_ratio": round(pe, 2) if pe else None, "pe_score": round(pe_score, 1),
        "pb_ratio": round(pb, 2) if pb else None, "pb_score": round(pb_score, 1),
        "ps_ratio": round(ps, 2) if ps else None, "ps_score": round(ps_score, 1),
    }

def _score_growth(info: dict) -> tuple[float, dict]:
    eg = info.get("earningsGrowth")
    eg_score = _clamp(eg / 0.5) * 10 if eg is not None else 0.0
    rg = info.get("revenueGrowth")
    rg_score = _clamp(rg / 0.30) * 8 if rg is not None else 0.0
    fwd, trail = info.get("forwardEps"), info.get("trailingEps")
    if fwd and trail and trail > 0:
        ig = (fwd - trail) / abs(trail)
        fwd_score = _clamp(ig / 0.30) * 7
    else:
        ig, fwd_score = None, 0.0
    total = eg_score + rg_score + fwd_score
    return total, {
        "total": round(total, 1),
        "earnings_growth_pct": round(eg * 100, 1) if eg is not None else None,
        "earnings_growth_score": round(eg_score, 1),
        "revenue_growth_pct": round(rg * 100, 1) if rg is not None else None,
        "revenue_growth_score": round(rg_score, 1),
        "implied_fwd_growth_pct": round(ig * 100, 1) if ig is not None else None,
        "fwd_growth_score": round(fwd_score, 1),
    }

def _score_quality(info: dict) -> tuple[float, dict]:
    roe = info.get("returnOnEquity")
    roe_score = _clamp(roe / 0.30) * 10 if roe is not None else 0.0
    margin = info.get("profitMargins") or info.get("grossMargins")
    margin_score = _clamp(margin / 0.30) * 8 if margin is not None else 0.0
    de = info.get("debtToEquity")
    if de is not None and de >= 0:
        de_r = de / 100
        de_score = _clamp(1 - de_r / 2.0) * 7
    else:
        de_r, de_score = None, 0.0
    total = roe_score + margin_score + de_score
    return total, {
        "total": round(total, 1),
        "roe_pct": round(roe * 100, 1) if roe is not None else None,
        "roe_score": round(roe_score, 1),
        "profit_margin_pct": round(margin * 100, 1) if margin is not None else None,
        "profit_margin_score": round(margin_score, 1),
        "debt_to_equity": round(de_r, 2) if de_r is not None else None,
        "debt_equity_score": round(de_score, 1),
    }

def _score_momentum(info: dict) -> tuple[float, dict]:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    high52, low52 = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow")
    if price and low52 and low52 > 0:
        ar = (price - low52) / low52
        ar_score = _clamp(ar / 1.0) * 15
    else:
        ar, ar_score = None, 0.0
    if price and high52 and low52 and (high52 - low52) > 0:
        prox = (price - low52) / (high52 - low52)
        prox_score = _clamp(prox) * 10
    else:
        prox, prox_score = None, 0.0
    total = ar_score + prox_score
    return total, {
        "total": round(total, 1),
        "annual_return_pct": round(ar * 100, 1) if ar is not None else None,
        "annual_return_score": round(ar_score, 1),
        "high_proximity_pct": round(prox * 100, 1) if prox is not None else None,
        "high_proximity_score": round(prox_score, 1),
    }

def score_stock(info: dict) -> dict:
    v_t, v_b = _score_value(info)
    g_t, g_b = _score_growth(info)
    q_t, q_b = _score_quality(info)
    m_t, m_b = _score_momentum(info)
    comp = v_t + g_t + q_t + m_t
    if   comp >= 80: sig, col = "STRONG BUY", "green"
    elif comp >= 65: sig, col = "BUY",        "lime"
    elif comp >= 50: sig, col = "WATCH",      "yellow"
    elif comp >= 35: sig, col = "AVOID",      "orange"
    else:            sig, col = "SKIP",       "red"
    return {"composite_score": round(comp, 1), "signal": sig, "signal_color": col,
            "value": v_b, "growth": g_b, "quality": q_b, "momentum": m_b}

# ── Mock data ─────────────────────────────────────────────────

_SECTORS = {
    # Mega-cap
    "AAPL":("Technology","Consumer Electronics"),"MSFT":("Technology","Software"),
    "NVDA":("Technology","Semiconductors"),"GOOGL":("Communication Services","Internet"),
    "GOOG":("Communication Services","Internet"),"META":("Communication Services","Social Media"),
    "AMZN":("Consumer Cyclical","Internet Retail"),"TSLA":("Consumer Cyclical","Auto Manufacturers"),
    # Semiconductors
    "AVGO":("Technology","Semiconductors"),"AMD":("Technology","Semiconductors"),
    "INTC":("Technology","Semiconductors"),"QCOM":("Technology","Semiconductors"),
    "TXN":("Technology","Semiconductors"),"MU":("Technology","Semiconductors"),
    "AMAT":("Technology","Semiconductor Equipment"),"LRCX":("Technology","Semiconductor Equipment"),
    "KLAC":("Technology","Semiconductor Equipment"),"MRVL":("Technology","Semiconductors"),
    "NXPI":("Technology","Semiconductors"),"ON":("Technology","Semiconductors"),
    "MCHP":("Technology","Semiconductors"),"ADI":("Technology","Semiconductors"),
    "SWKS":("Technology","Semiconductors"),"QRVO":("Technology","Semiconductors"),
    "MPWR":("Technology","Semiconductors"),"ENTG":("Technology","Semiconductor Equipment"),
    "COHR":("Technology","Photonics"),"RMBS":("Technology","Semiconductors"),
    "WOLF":("Technology","Semiconductors"),"AMBA":("Technology","Semiconductors"),
    "SMCI":("Technology","Computer Hardware"),"MTSI":("Technology","Semiconductors"),
    "ALGM":("Technology","Semiconductors"),"POWI":("Technology","Semiconductors"),
    "DIOD":("Technology","Semiconductors"),"SLAB":("Technology","Semiconductors"),
    "FORM":("Technology","Semiconductor Equipment"),"CEVA":("Technology","Semiconductors"),
    # Enterprise software & cloud
    "ORCL":("Technology","Enterprise Software"),"ADBE":("Technology","Creative Software"),
    "CRM":("Technology","CRM Software"),"NOW":("Technology","ITSM Software"),
    "WDAY":("Technology","HR Software"),"TEAM":("Technology","Developer Tools"),
    "SNOW":("Technology","Cloud Data"),"DDOG":("Technology","Observability"),
    "NET":("Technology","Networking"),"CRWD":("Technology","Cybersecurity"),
    "PANW":("Technology","Cybersecurity"),"FTNT":("Technology","Cybersecurity"),
    "ZS":("Technology","Cybersecurity"),"OKTA":("Technology","Identity Security"),
    "MDB":("Technology","Database"),"ESTC":("Technology","Search & Analytics"),
    "CFLT":("Technology","Data Streaming"),"GTLB":("Technology","Developer Tools"),
    "HUBS":("Technology","Marketing Software"),"BILL":("Technology","Financial Software"),
    "VEEV":("Technology","Life Sciences Software"),"ANSS":("Technology","Simulation Software"),
    "CDNS":("Technology","EDA Software"),"SNPS":("Technology","EDA Software"),
    "MANH":("Technology","Supply Chain Software"),"PCOR":("Technology","Construction Software"),
    "TYL":("Technology","GovTech"),"ADP":("Technology","HR Software"),
    "PAYX":("Technology","Payroll Software"),"CSCO":("Technology","Networking"),
    "FFIV":("Technology","Application Delivery"),"CIEN":("Technology","Optical Networking"),
    "CALX":("Technology","Broadband Equipment"),"APPN":("Technology","Low-Code Platform"),
    "SMAR":("Technology","Work Management"),"NCNO":("Technology","Banking Software"),
    "JAMF":("Technology","Device Management"),"DOMO":("Technology","BI Software"),
    "ASAN":("Technology","Work Management"),"BRZE":("Technology","Marketing Automation"),
    "ZI":("Technology","Sales Intelligence"),"MNDY":("Technology","Work Management"),
    "FROG":("Technology","DevOps"),"DT":("Technology","Observability"),
    "TOST":("Technology","Restaurant Software"),"AI":("Technology","Enterprise AI"),
    "PLTR":("Technology","Data Analytics"),"TTD":("Technology","AdTech"),
    "APP":("Technology","Mobile Marketing"),"RAMP":("Technology","Data Connectivity"),
    # Internet & e-commerce
    "EBAY":("Consumer Cyclical","E-Commerce"),"ETSY":("Consumer Cyclical","Marketplace"),
    "CHWY":("Consumer Cyclical","Pet E-Commerce"),"PINS":("Communication Services","Social Media"),
    "SNAP":("Communication Services","Social Media"),"MTCH":("Communication Services","Dating Apps"),
    "IAC":("Communication Services","Internet"),"ABNB":("Consumer Cyclical","Travel"),
    "BKNG":("Consumer Cyclical","Travel"),"EXPE":("Consumer Cyclical","Travel"),
    "TRIP":("Consumer Cyclical","Travel"),"CARG":("Consumer Cyclical","Auto Marketplace"),
    "ANGI":("Consumer Cyclical","Home Services"),"OPEN":("Real Estate","iBuying"),
    "RDFN":("Real Estate","Real Estate Tech"),"Z":("Real Estate","Real Estate Portal"),
    "MELI":("Consumer Cyclical","Latin America E-Commerce"),"PDD":("Consumer Cyclical","E-Commerce"),
    "JD":("Consumer Cyclical","China E-Commerce"),"BABA":("Consumer Cyclical","China E-Commerce"),
    "STNE":("Financial Services","Brazil Fintech"),"PAGS":("Financial Services","Brazil Fintech"),
    # Streaming & media
    "NFLX":("Communication Services","Streaming"),"ROKU":("Communication Services","Streaming"),
    "SPOT":("Communication Services","Music Streaming"),"SIRI":("Communication Services","Radio"),
    "CHTR":("Communication Services","Cable"),"CMCSA":("Communication Services","Cable & Media"),
    "WBD":("Communication Services","Media"),"PARA":("Communication Services","Media"),
    # Healthcare: biotech & pharma
    "AMGN":("Healthcare","Biotechnology"),"GILD":("Healthcare","Antivirals"),
    "BIIB":("Healthcare","Neurology"),"VRTX":("Healthcare","Cystic Fibrosis"),
    "ILMN":("Healthcare","Genomics"),"IDXX":("Healthcare","Animal Health"),
    "ISRG":("Healthcare","Surgical Robotics"),"ALGN":("Healthcare","Dental Devices"),
    "DXCM":("Healthcare","Glucose Monitoring"),"HOLX":("Healthcare","Women's Health"),
    "BMRN":("Healthcare","Rare Disease"),"EXAS":("Healthcare","Cancer Diagnostics"),
    "NTRA":("Healthcare","Genomics"),"NVAX":("Healthcare","Vaccines"),
    "BNTX":("Healthcare","mRNA Vaccines"),"MRNA":("Healthcare","mRNA Vaccines"),
    "INCY":("Healthcare","Oncology"),"SRPT":("Healthcare","Rare Disease"),
    "IONS":("Healthcare","RNA Therapeutics"),"CRSP":("Healthcare","Gene Editing"),
    "EDIT":("Healthcare","Gene Editing"),"NTLA":("Healthcare","Gene Editing"),
    "BEAM":("Healthcare","Gene Editing"),"ALNY":("Healthcare","RNA Therapeutics"),
    "RARE":("Healthcare","Rare Disease"),"ACAD":("Healthcare","Neurology"),
    "FATE":("Healthcare","Cell Therapy"),"RCUS":("Healthcare","Oncology"),
    "ARQT":("Healthcare","Dermatology"),"CDNA":("Healthcare","Transplant Diagnostics"),
    "PODD":("Healthcare","Insulin Delivery"),"TNDM":("Healthcare","Insulin Delivery"),
    "INSP":("Healthcare","Sleep Apnea"),"NVCR":("Healthcare","Oncology Devices"),
    "MMSI":("Healthcare","Vascular Devices"),"PACB":("Healthcare","Genomics"),
    "RXRX":("Healthcare","AI Drug Discovery"),"OFIX":("Healthcare","Orthopedics"),
    "ADUS":("Healthcare","Home Health"),"RGEN":("Healthcare","Bioprocessing"),
    # Financial services
    "PYPL":("Financial Services","Digital Payments"),"SQ":("Financial Services","Fintech"),
    "AFRM":("Financial Services","BNPL"),"UPST":("Financial Services","AI Lending"),
    "SOFI":("Financial Services","Neobank"),"MKTX":("Financial Services","Bond Trading"),
    "IBKR":("Financial Services","Online Brokerage"),"LPLA":("Financial Services","Wealth Management"),
    "SEIC":("Financial Services","Asset Management"),"NDAQ":("Financial Services","Exchanges"),
    "CBOE":("Financial Services","Exchanges"),"MORN":("Financial Services","Financial Data"),
    "VRSK":("Financial Services","Risk Analytics"),"MSCI":("Financial Services","Indices"),
    "FDS":("Financial Services","Financial Data"),"HOOD":("Financial Services","Retail Brokerage"),
    "FUTU":("Financial Services","Online Brokerage"),"MQ":("Financial Services","Card Issuing"),
    "LC":("Financial Services","Marketplace Lending"),"EVTC":("Financial Services","Payments"),
    # Consumer
    "SBUX":("Consumer Cyclical","Restaurants"),"MNST":("Consumer Defensive","Energy Drinks"),
    "MDLZ":("Consumer Defensive","Snacks"),"KHC":("Consumer Defensive","Packaged Foods"),
    "COST":("Consumer Defensive","Warehouse Retail"),"WBA":("Consumer Defensive","Pharmacy"),
    "DLTR":("Consumer Defensive","Dollar Stores"),"FIVE":("Consumer Cyclical","Specialty Retail"),
    "OLLI":("Consumer Cyclical","Closeout Retail"),"LULU":("Consumer Cyclical","Athleisure"),
    "CROX":("Consumer Cyclical","Footwear"),"DECK":("Consumer Cyclical","Footwear"),
    "SKX":("Consumer Cyclical","Footwear"),"UAA":("Consumer Cyclical","Sportswear"),
    "COLM":("Consumer Cyclical","Outdoor Apparel"),"TSCO":("Consumer Cyclical","Farm & Ranch"),
    "ULTA":("Consumer Cyclical","Beauty Retail"),"ORLY":("Consumer Cyclical","Auto Parts"),
    "BBWI":("Consumer Cyclical","Personal Care"),"CASY":("Consumer Cyclical","Convenience Stores"),
    "WING":("Consumer Cyclical","Restaurants"),"SHAK":("Consumer Cyclical","Restaurants"),
    "BLMN":("Consumer Cyclical","Restaurants"),"TXRH":("Consumer Cyclical","Restaurants"),
    "EAT":("Consumer Cyclical","Restaurants"),"JACK":("Consumer Cyclical","Restaurants"),
    "NTES":("Communication Services","China Internet"),"BIDU":("Communication Services","China Search"),
    # Industrials
    "FAST":("Industrials","Industrial Distribution"),"POOL":("Industrials","Pool Equipment"),
    "AAON":("Industrials","HVAC"),"NDSN":("Industrials","Dispensing Equipment"),
    # Clean energy
    "ENPH":("Technology","Solar Inverters"),"SEDG":("Technology","Solar Inverters"),
    "FSLR":("Technology","Solar Panels"),"SPWR":("Technology","Solar"),
    "RUN":("Technology","Residential Solar"),"NOVA":("Technology","Residential Solar"),
    "STEM":("Technology","Energy Storage"),
    # Electric vehicles
    "RIVN":("Consumer Cyclical","Electric Trucks"),"LCID":("Consumer Cyclical","Luxury EV"),
    "FSR":("Consumer Cyclical","Electric Vehicles"),"NKLA":("Industrials","Hydrogen Trucks"),
    # IT services
    "CTSH":("Technology","IT Services"),"INFY":("Technology","IT Services"),
    "WIT":("Technology","IT Services"),"EPAM":("Technology","IT Services"),
    "GLOB":("Technology","IT Services"),
}
_NAMES = {
    "AAPL":"Apple Inc.","MSFT":"Microsoft Corporation","NVDA":"NVIDIA Corporation",
    "GOOGL":"Alphabet Inc.","GOOG":"Alphabet Inc.","META":"Meta Platforms Inc.",
    "AMZN":"Amazon.com Inc.","TSLA":"Tesla Inc.","AVBO":"Broadcom Inc.",
    "AVBO":"Broadcom Inc.","AVGO":"Broadcom Inc.","AMD":"Advanced Micro Devices",
    "INTC":"Intel Corporation","QCOM":"Qualcomm Inc.","TXN":"Texas Instruments",
    "MU":"Micron Technology","AMAT":"Applied Materials","LRCX":"Lam Research",
    "KLAC":"KLA Corporation","MRVL":"Marvell Technology","NXPI":"NXP Semiconductors",
    "ON":"ON Semiconductor","MCHP":"Microchip Technology","ADI":"Analog Devices",
    "SWKS":"Skyworks Solutions","QRVO":"Qorvo Inc.","MPWR":"Monolithic Power Systems",
    "ENTG":"Entegris Inc.","COHR":"Coherent Corp.","RMBS":"Rambus Inc.",
    "WOLF":"Wolfspeed Inc.","AMBA":"Ambarella Inc.","SMCI":"Super Micro Computer",
    "MTSI":"MACOM Technology","ALGM":"Allegro MicroSystems","POWI":"Power Integrations",
    "DIOD":"Diodes Inc.","SLAB":"Silicon Laboratories","FORM":"FormFactor Inc.","CEVA":"CEVA Inc.",
    "ORCL":"Oracle Corporation","ADBE":"Adobe Inc.","CRM":"Salesforce Inc.",
    "NOW":"ServiceNow Inc.","WDAY":"Workday Inc.","TEAM":"Atlassian Corporation",
    "SNOW":"Snowflake Inc.","DDOG":"Datadog Inc.","NET":"Cloudflare Inc.",
    "CRWD":"CrowdStrike Holdings","PANW":"Palo Alto Networks","FTNT":"Fortinet Inc.",
    "ZS":"Zscaler Inc.","OKTA":"Okta Inc.","MDB":"MongoDB Inc.","ESTC":"Elastic N.V.",
    "CFLT":"Confluent Inc.","GTLB":"GitLab Inc.","HUBS":"HubSpot Inc.",
    "BILL":"Bill.com Holdings","VEEV":"Veeva Systems","ANSS":"ANSYS Inc.",
    "CDNS":"Cadence Design Systems","SNPS":"Synopsys Inc.","MANH":"Manhattan Associates",
    "PCOR":"Procore Technologies","TYL":"Tyler Technologies",
    "ADP":"Automatic Data Processing","PAYX":"Paychex Inc.","CSCO":"Cisco Systems",
    "FFIV":"F5 Inc.","CIEN":"Ciena Corporation","CALX":"Calix Inc.",
    "APPN":"Appian Corporation","SMAR":"Smartsheet Inc.","NCNO":"nCino Inc.",
    "JAMF":"Jamf Holding","DOMO":"Domo Inc.","ASAN":"Asana Inc.","BRZE":"Braze Inc.",
    "ZI":"ZoomInfo Technologies","MNDY":"Monday.com","FROG":"JFrog Ltd.",
    "DT":"Dynatrace Inc.","TOST":"Toast Inc.","AI":"C3.ai Inc.",
    "PLTR":"Palantir Technologies","TTD":"The Trade Desk","APP":"Applovin Corporation",
    "RAMP":"LiveRamp Holdings",
    "EBAY":"eBay Inc.","ETSY":"Etsy Inc.","CHWY":"Chewy Inc.","PINS":"Pinterest Inc.",
    "SNAP":"Snap Inc.","MTCH":"Match Group","IAC":"IAC Inc.","ABNB":"Airbnb Inc.",
    "BKNG":"Booking Holdings","EXPE":"Expedia Group","TRIP":"Tripadvisor Inc.",
    "CARG":"CarGurus Inc.","ANGI":"Angi Inc.","OPEN":"Opendoor Technologies",
    "RDFN":"Redfin Corporation","Z":"Zillow Group","MELI":"MercadoLibre Inc.",
    "PDD":"PDD Holdings","JD":"JD.com Inc.","BABA":"Alibaba Group",
    "STNE":"StoneCo Ltd.","PAGS":"PagSeguro Digital",
    "NFLX":"Netflix Inc.","ROKU":"Roku Inc.","SPOT":"Spotify Technology",
    "SIRI":"Sirius XM Holdings","CHTR":"Charter Communications",
    "CMCSA":"Comcast Corporation","WBD":"Warner Bros. Discovery","PARA":"Paramount Global",
    "AMGN":"Amgen Inc.","GILD":"Gilead Sciences","BIIB":"Biogen Inc.",
    "VRTX":"Vertex Pharmaceuticals","ILMN":"Illumina Inc.","IDXX":"IDEXX Laboratories",
    "ISRG":"Intuitive Surgical","ALGN":"Align Technology","DXCM":"DexCom Inc.",
    "HOLX":"Hologic Inc.","BMRN":"BioMarin Pharmaceutical","EXAS":"Exact Sciences",
    "NTRA":"Natera Inc.","NVAX":"Novavax Inc.","BNTX":"BioNTech SE","MRNA":"Moderna Inc.",
    "INCY":"Incyte Corporation","SRPT":"Sarepta Therapeutics","IONS":"Ionis Pharmaceuticals",
    "CRSP":"CRISPR Therapeutics","EDIT":"Editas Medicine","NTLA":"Intellia Therapeutics",
    "BEAM":"Beam Therapeutics","ALNY":"Alnylam Pharmaceuticals","RARE":"Ultragenyx Pharmaceutical",
    "ACAD":"Acadia Pharmaceuticals","FATE":"Fate Therapeutics","RCUS":"Arcus Biosciences",
    "ARQT":"Arcutis Biotherapeutics","CDNA":"CareDx Inc.","PODD":"Insulet Corporation",
    "TNDM":"Tandem Diabetes Care","INSP":"Inspire Medical Systems","NVCR":"NovoCure Ltd.",
    "MMSI":"Merit Medical Systems","PACB":"Pacific Biosciences","RXRX":"Recursion Pharmaceuticals",
    "OFIX":"Orthofix Medical","ADUS":"Addus HomeCare","RGEN":"Repligen Corporation",
    "PYPL":"PayPal Holdings","SQ":"Block Inc.","AFRM":"Affirm Holdings",
    "UPST":"Upstart Holdings","SOFI":"SoFi Technologies","MKTX":"MarketAxess Holdings",
    "IBKR":"Interactive Brokers","LPLA":"LPL Financial Holdings","SEIC":"SEI Investments",
    "NDAQ":"Nasdaq Inc.","CBOE":"Cboe Global Markets","MORN":"Morningstar Inc.",
    "VRSK":"Verisk Analytics","MSCI":"MSCI Inc.","FDS":"FactSet Research Systems",
    "HOOD":"Robinhood Markets","FUTU":"Futu Holdings","MQ":"Marqeta Inc.",
    "LC":"LendingClub Corporation","EVTC":"EVERTEC Inc.",
    "SBUX":"Starbucks Corporation","MNST":"Monster Beverage","MDLZ":"Mondelez International",
    "KHC":"Kraft Heinz Company","COST":"Costco Wholesale","WBA":"Walgreens Boots Alliance",
    "DLTR":"Dollar Tree Inc.","FIVE":"Five Below Inc.","OLLI":"Ollie's Bargain Outlet",
    "LULU":"Lululemon Athletica","CROX":"Crocs Inc.","DECK":"Deckers Outdoor",
    "SKX":"Skechers USA","UAA":"Under Armour","COLM":"Columbia Sportswear",
    "TSCO":"Tractor Supply","ULTA":"Ulta Beauty","ORLY":"O'Reilly Automotive",
    "BBWI":"Bath & Body Works","CASY":"Casey's General Stores","WING":"Wingstop Inc.",
    "SHAK":"Shake Shack Inc.","BLMN":"Bloomin' Brands","TXRH":"Texas Roadhouse",
    "EAT":"Brinker International","JACK":"Jack in the Box",
    "NTES":"NetEase Inc.","BIDU":"Baidu Inc.",
    "FAST":"Fastenal Company","POOL":"Pool Corporation","AAON":"AAON Inc.",
    "NDSN":"Nordson Corporation",
    "ENPH":"Enphase Energy","SEDG":"SolarEdge Technologies","FSLR":"First Solar Inc.",
    "SPWR":"SunPower Corporation","RUN":"Sunrun Inc.","NOVA":"Sunnova Energy","STEM":"Stem Inc.",
    "RIVN":"Rivian Automotive","LCID":"Lucid Group","FSR":"Fisker Inc.","NKLA":"Nikola Corporation",
    "CTSH":"Cognizant Technology","INFY":"Infosys Limited","WIT":"Wipro Limited",
    "EPAM":"EPAM Systems","GLOB":"Globant S.A.",
}
MOCK_TICKERS = list(_SECTORS.keys())

def mock_info(ticker: str) -> dict:
    rng = random.Random(sum(ord(c) * (i + 1) for i, c in enumerate(ticker)))
    sector, industry = _SECTORS.get(ticker, ("Technology", "Software"))
    price = round(rng.uniform(20, 800), 2)
    low52 = round(price * rng.uniform(0.50, 0.85), 2)
    high52 = round(price * rng.uniform(1.05, 1.80), 2)
    if ticker in {"AAPL","MSFT","NVDA","GOOGL","AMZN","META"}:
        mktcap = int(rng.uniform(1e12, 3e12))
    elif ticker in {"TSLA","AVGO","ORCL","AMD","ADBE","CRM","NFLX","COST"}:
        mktcap = int(rng.uniform(1e11, 8e11))
    else:
        mktcap = int(rng.uniform(5e9, 5e10))
    trailing_eps = round(rng.uniform(0.5, 25), 2)
    return {
        "longName": _NAMES.get(ticker, ticker + " Inc."),
        "sector": sector, "industry": industry, "quoteType": "EQUITY",
        "currentPrice": price, "regularMarketPrice": price,
        "fiftyTwoWeekHigh": high52, "fiftyTwoWeekLow": low52,
        "marketCap": mktcap,
        "forwardPE": round(rng.uniform(8, 80), 2),
        "trailingPE": round(rng.uniform(10, 90), 2),
        "priceToBook": round(rng.uniform(0.8, 20), 2),
        "priceToSalesTrailing12Months": round(rng.uniform(0.5, 15), 2),
        "earningsGrowth": round(rng.uniform(-0.15, 0.65), 3),
        "revenueGrowth": round(rng.uniform(-0.05, 0.45), 3),
        "forwardEps": round(trailing_eps * (1 + rng.uniform(-0.10, 0.40)), 2),
        "trailingEps": trailing_eps,
        "returnOnEquity": round(rng.uniform(-0.05, 0.80), 3),
        "profitMargins": round(rng.uniform(-0.02, 0.55), 3),
        "grossMargins": round(rng.uniform(0.10, 0.75), 3),
        "debtToEquity": round(rng.uniform(0, 300), 1),
        "averageVolume": int(rng.uniform(1e6, 50e6)),
        "dividendYield": round(rng.uniform(0, 0.025), 4) if rng.random() > 0.6 else None,
        "currency": "USD", "exchange": "NASDAQ",
    }

# ── Live Yahoo Finance (best-effort, per-request) ─────────────

def _try_live(ticker: str) -> dict | None:
    try:
        import requests as req
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
               "?modules=price,defaultKeyStatistics,financialData,summaryDetail,assetProfile")
        resp = req.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return None
        result = (resp.json().get("quoteSummary") or {}).get("result") or []
        if not result:
            return None
        b = result[0]
        price   = b.get("price", {})
        kstats  = b.get("defaultKeyStatistics", {})
        fin     = b.get("financialData", {})
        summary = b.get("summaryDetail", {})
        profile = b.get("assetProfile", {})
        def raw(d, k):
            v = d.get(k)
            return v.get("raw") if isinstance(v, dict) else v
        cur_price = raw(fin, "currentPrice") or raw(price, "regularMarketPrice")
        if not cur_price:
            return None
        return {
            "longName": price.get("longName"), "shortName": price.get("shortName"),
            "sector": profile.get("sector"), "industry": profile.get("industry"),
            "quoteType": price.get("quoteType"),
            "currentPrice": cur_price, "regularMarketPrice": cur_price,
            "fiftyTwoWeekHigh": raw(summary, "fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": raw(summary, "fiftyTwoWeekLow"),
            "marketCap": raw(price, "marketCap"),
            "forwardPE": raw(summary, "forwardPE"),
            "trailingPE": raw(summary, "trailingPE"),
            "priceToBook": raw(kstats, "priceToBook"),
            "priceToSalesTrailing12Months": raw(summary, "priceToSalesTrailing12Months"),
            "earningsGrowth": raw(fin, "earningsGrowth"),
            "revenueGrowth": raw(fin, "revenueGrowth"),
            "forwardEps": raw(kstats, "forwardEps"),
            "trailingEps": raw(kstats, "trailingEps"),
            "returnOnEquity": raw(fin, "returnOnEquity"),
            "profitMargins": raw(fin, "profitMargins"),
            "grossMargins": raw(fin, "grossMargins"),
            "debtToEquity": raw(fin, "debtToEquity"),
            "averageVolume": raw(summary, "averageVolume"),
            "dividendYield": raw(summary, "dividendYield"),
            "currency": price.get("currency", "USD"),
            "exchange": price.get("exchangeName", "NASDAQ"),
        }
    except Exception:
        return None

# ── Cache ─────────────────────────────────────────────────────

def _cache_get(ticker: str) -> dict | None:
    e = _CACHE.get(ticker)
    if e and (time.time() - e.get("_ts", 0)) < _CACHE_TTL:
        return {k: v for k, v in e.items() if k != "_ts"}
    return None

def _cache_set(ticker: str, data: dict) -> None:
    _CACHE[ticker] = {**data, "_ts": time.time()}

# ── Core ──────────────────────────────────────────────────────

def _fetch_and_score(ticker: str) -> dict[str, Any] | None:
    cached = _cache_get(ticker)
    if cached:
        return cached
    live = _try_live(ticker)
    info = live if live else mock_info(ticker)
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return None
    scores = score_stock(info)
    result: dict[str, Any] = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName", ticker),
        "sector": info.get("sector") or "Unknown",
        "industry": info.get("industry") or "Unknown",
        "market_cap": info.get("marketCap"),
        "price": price,
        "currency": info.get("currency", "USD"),
        "exchange": info.get("exchange", "NASDAQ"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "avg_volume": info.get("averageVolume"),
        "dividend_yield": info.get("dividendYield"),
        "demo_mode": live is None,
        **scores,
    }
    _cache_set(ticker, result)
    return result

# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    p = STATIC_DIR / "index.html"
    if p.exists():
        return Response(p.read_text(), mimetype="text/html")
    return Response("<h1>Stock Picker loading…</h1>", mimetype="text/html")

@app.route("/api/status")
def status():
    return jsonify({"demo_mode": True, "message": "Stock Picker API is running."})

@app.route("/api/criteria")
def criteria():
    return jsonify({
        "title": "Stock Picking Scoring Methodology",
        "description": "Each stock is scored 0–100 across four equally-weighted pillars.",
        "pillars": [
            {"name": "Value", "max_points": 25,
             "description": "Is the stock priced cheaply relative to its fundamentals?",
             "metrics": [
                 {"metric": "Forward P/E",        "max": 10, "ideal": "< 15",  "bad": "> 50",  "rationale": "Lower P/E = paying less per dollar of earnings."},
                 {"metric": "Price-to-Book (P/B)", "max": 8,  "ideal": "< 1.5", "bad": "> 5",  "rationale": "P/B < 1 means buying assets below book value."},
                 {"metric": "Price-to-Sales (P/S)","max": 7,  "ideal": "< 1",   "bad": "> 6",  "rationale": "Low P/S stocks are often undervalued vs revenue."},
             ]},
            {"name": "Growth", "max_points": 25,
             "description": "Is the business growing earnings and revenue meaningfully?",
             "metrics": [
                 {"metric": "EPS Growth (YoY)",            "max": 10, "ideal": "> 50%", "bad": "< 0%",         "rationale": "Accelerating earnings growth drives appreciation."},
                 {"metric": "Revenue Growth (YoY)",        "max": 8,  "ideal": "> 30%", "bad": "< 0%",         "rationale": "Top-line growth is the foundation of long-term value."},
                 {"metric": "Forward EPS vs Trailing EPS", "max": 7,  "ideal": "> 30%", "bad": "Flat/declining","rationale": "Analyst consensus signals market confidence."},
             ]},
            {"name": "Quality", "max_points": 25,
             "description": "Is the business high-quality with a strong balance sheet?",
             "metrics": [
                 {"metric": "Return on Equity (ROE)", "max": 10, "ideal": "> 30%", "bad": "< 0%",  "rationale": "High ROE = efficient use of shareholder capital."},
                 {"metric": "Profit Margin",          "max": 8,  "ideal": "> 30%", "bad": "< 0%",  "rationale": "Wide margins = pricing power & durability."},
                 {"metric": "Debt / Equity",          "max": 7,  "ideal": "< 0.3", "bad": "> 2.0", "rationale": "Low leverage reduces risk."},
             ]},
            {"name": "Momentum", "max_points": 25,
             "description": "Is price action confirming the fundamental thesis?",
             "metrics": [
                 {"metric": "52-Week Return (vs low)", "max": 15, "ideal": "> 100%",   "bad": "Near low",  "rationale": "Strong trends often persist medium-term."},
                 {"metric": "52-Week High Proximity",  "max": 10, "ideal": "Near high", "bad": "Near low",  "rationale": "Stocks near highs exhibit relative strength."},
             ]},
        ],
        "signals": [
            {"range": "80–100", "label": "STRONG BUY"}, {"range": "65–79", "label": "BUY"},
            {"range": "50–64", "label": "WATCH"},       {"range": "35–49", "label": "AVOID"},
            {"range": "0–34",  "label": "SKIP"},
        ],
    })

@app.route("/api/scan")
def scan():
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    sector_f = request.args.get("sector", "").strip().lower()
    min_sc   = float(request.args.get("min_score", 0))
    max_sc   = float(request.args.get("max_score", 100))
    signal_f = request.args.get("signal", "").strip().upper()
    sort_by  = request.args.get("sort_by", "composite_score")
    sort_dir = request.args.get("sort_dir", "desc")
    custom   = request.args.get("tickers", "").strip()
    force    = request.args.get("refresh", "false").lower() == "true"

    ticker_list = ([t.strip().upper() for t in custom.split(",") if t.strip()]
                   if custom else MOCK_TICKERS)
    if force:
        for t in ticker_list:
            _CACHE.pop(t, None)

    results: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_fetch_and_score, t): t for t in ticker_list}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.append(r)
    elapsed = round(time.time() - t0, 2)

    if sector_f:
        results = [r for r in results if sector_f in (r.get("sector") or "").lower()]
    results = [r for r in results if min_sc <= r["composite_score"] <= max_sc]
    if signal_f:
        results = [r for r in results if r.get("signal") == signal_f]

    reverse = sort_dir != "asc"
    results.sort(
        key=lambda r: r.get(sort_by) if r.get(sort_by) is not None
        else (-math.inf if reverse else math.inf),
        reverse=reverse,
    )
    total       = len(results)
    total_pages = max(1, math.ceil(total / per_page))
    start       = (page - 1) * per_page
    return jsonify({
        "page": page, "per_page": per_page, "total": total,
        "total_pages": total_pages, "elapsed_seconds": elapsed,
        "sectors": sorted({r.get("sector", "Unknown") for r in results if r.get("sector")}),
        "results": results[start: start + per_page],
    })

@app.route("/api/stock/<ticker>")
def stock_detail(ticker: str):
    ticker = ticker.upper().strip()
    if request.args.get("refresh", "false").lower() == "true":
        _CACHE.pop(ticker, None)
    result = _fetch_and_score(ticker)
    if result is None:
        return jsonify({"error": f"No data found for {ticker}"}), 404
    return jsonify(result)
