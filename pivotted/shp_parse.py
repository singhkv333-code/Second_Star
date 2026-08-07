"""Parse a SEBI Reg-31 shareholding-pattern XBRL into flat rows.

Pure function of the document text — no network, no DB — so it can be tested
against a fixture and reused by any caller.

WHAT THE DOCUMENT LOOKS LIKE (measured 2026-08-08 on RELIANCE/JPPOWER)

  Every fact carries a contextRef. The context is what gives it meaning:

    no dimension                      -> document-level (company name, ISIN)
    explicitMember CategoryOfShareholdersAxis
                                      -> a TABLE ROW: "Promoter & Promoter
                                         Group", "Mutual Funds", "Public" …
    typedMember  DetailsOfSharesHeldBy<X>Axis
                                      -> ONE NAMED SHAREHOLDER, keyed by an
                                         opaque id ("OthersIndianShareholders_
                                         Context15"). The axis names the bucket.
    typedMember  SignificantBeneficialOwnersAxis
                                      -> the natural person behind a corporate
                                         promoter entity.

  Each named shareholder occupies TWO contexts that share the typed key: a
  DURATION one holding name / PAN / promoter-type, and an INSTANT one holding
  every number. We merge them on the key, which is why `holders` is keyed by
  the typed value and not by context id.

THREE THINGS THAT WILL BITE

  1. Percentages are FRACTIONS, and they are a share of the HOLDER'S OWN
     stake, not of the company. JPPOWER Jun-2026: pledged 1,200,509,465 of a
     1,644,830,118 holding = 0.7299. Rendering that as "0.73%" instead of
     "72.99% of the promoter stake" is the difference between a clean company
     and one that is three-quarters hocked.

  2. Encumbrance is split THREE ways from taxonomy 2025-05-31 onward
     (pledged / non-disposal undertaking / other) and was a single number
     before it. Vedanta Jun-2026 reports pledged=false while carrying
     2,032,309,058 shares under "other encumbrances" — 99.99% of one promoter
     entity. Only the SUM is safe to call "encumbered", so `enc_total` is
     recomputed from the parts when the filer left it out.

  3. NSE serves the same document with `PermanentAccountNumberOfShareholder`
     AND `TypeOfPromoterShareholding` masked to '******'. That second one is
     the promoter flag — an NSE-sourced filing cannot tell you which named
     holder is a promoter. We map '******' to None so the gap is explicit
     rather than a literal row of asterisks in the database.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

MASK = "******"

# XBRL tag -> our column. Anything not listed is ignored, which keeps the
# schema stable when SEBI adds a tag mid-year.
NUM = {
    "NumberOfShareholders": "shareholders",
    "NumberOfFullyPaidUpEquityShares": "shares_fully_paid",
    "NumberOfPartlyPaidUpEquityShares": "shares_partly_paid",
    "NumberOfShares": "shares",
    "ShareholdingAsAPercentageOfTotalNumberOfShares": "pct",
    "NumberOfVotingRights": "voting_rights",
    "PercentageOfTotalVotingRights": "pct_voting",
    "NumberOfSharesOnFullyDilutedBasisIncludingWarrantsESOPAndConvertibleSecurities": "shares_diluted",
    "ShareholdingAsAPercentageAssumingFullConversionOfConvertibleSecuritiesWarrantsAndESOP": "pct_diluted",
    "NumberOfEquitySharesHeldInDematerializedForm": "demat",
    "NumberOfTheLockedInShares": "locked_in",
    "LockedInSharesAsAPercentageOfTotalNumberOfShares": "locked_in_pct",
    "NumberOfSharesUnderlyingOutstandingConvertibleSecuritiesWarrantsAndESOP": "convertibles",
    "NumberOfSharesOutstandingESOPGranted": "esop_outstanding",
    "NumberOfSharesUnderlyingOutstandingDepositoryReceipts": "depository_receipts",
    "NumberOfSharesUnderSubCategoryOne": "sub_category_1",
    "NumberOfSharesUnderSubCategoryTwo": "sub_category_2",
    "NumberOfSharesUnderSubCategoryThree": "sub_category_3",
    # encumbrance — taxonomy 2025-05-31 and later
    "NumberOfSharesEncumberedUnderPledged": "enc_pledged",
    "NumberOfSharesEncumberedUnderNonDisposalUndertaking": "enc_ndu",
    "NumberOfSharesEncumberedUnderOtherEncumbrances": "enc_other",
    "NumberOfSharesEncumbered": "enc_total",
    "EncumberedSharesHeldAsPercentageOfTotalNumberOfShares": "enc_pct",
    # encumbrance — taxonomy 2016-06-23 .. 2022-09-30 (single number)
    "PledgedOrEncumberedNumberOfShares": "enc_total",
    "PledgedOrEncumberedSharesHeldAsPercentageOfTotalNumberOfShares": "enc_pct",
}

TXT = {
    "NameOfTheShareholder": "name",
    "TypeOfPromoterShareholding": "promoter_type",
    "CategoryOfOtherIndianShareholders": "holder_category",
    "CategoryOfOtherNonInstitutions": "holder_category",
    "CategoryOfOtherInstitutions": "holder_category",
    "WhetherACategoryOrMoreThan1PercentageOfShareholding": "over_one_pct",
}

SBO = {
    "NameOfSignificantBeneficialOwners": "sbo_name",
    "NationalityOfSignificantBeneficialOwners": "sbo_nationality",
    "NameOfRegisteredOwner": "registered_owner",
    "NationalityOfRegisteredOwner": "registered_owner_nationality",
    "DateOfCreationOrAcquisitionOfSignificantBeneficialInterest": "held_since",
    "DetailsOfHoldingExerciseOfRightOfTheSBOInTheReportingCompanyWhetherByVirtueOfShares": "by_shares",
    "DetailsOfHoldingExerciseOfRightOfTheSBOInTheReportingCompanyWhetherByVirtueOfVotingRights": "by_voting_rights",
    "DetailsOfHoldingExerciseOfRightOfTheSBOInTheReportingCompanyWhetherByVirtueOfRightsOnDistributableDividendOrAnyOtherDistribution": "by_dividend",
    "DetailsOfHoldingExerciseOfRightOfTheSBOInTheReportingCompanyWhetherByVirtueOfExerciseOfControl": "by_control",
    "DetailsOfHoldingExerciseOfRightOfTheSBOInTheReportingCompanyWhetherByVirtueOfExerciseOfSignificantInfluence": "by_significant_influence",
}

META = {
    "NameOfTheCompany": "company_name",
    "ISIN": "isin",
    "ScripCode": "scripcode",
    "Symbol": "symbol",
    "MSEISymbol": "msei_symbol",
    "DateOfReport": "date_of_report",
    "ShareholdingPatternFiledUnder": "filed_under",
    "TypeOfReport": "report_type",
    "ClassOfSecurity": "security_class",
    "WhetherCompanyIsSME": "is_sme",
    "WhetherTheListedEntityIsPublicSectorUndertaking": "is_psu",
    "WhetherTheListedEntityHasAnySignificantBeneficialOwner": "has_sbo",
}

_TAX = re.compile(r"xbrl/shp/(\d{4}-\d{2}-\d{2})")


def _local(tag: str) -> str:
    """Strip BOTH namespace forms.

    Element tags arrive expanded as '{http://…}NumberOfShares', but the
    `dimension` attribute and explicitMember text keep the raw prefix form
    'in-bse-shp:CategoryOfShareholdersAxis'. Handling only the first silently
    yields zero categories and zero SBO rows — the facts parse, they just
    never match an axis.
    """
    return (tag or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _clean(v):
    v = (v or "").strip()
    if not v or v == MASK:
        return None
    return v


def _num(v):
    v = _clean(v)
    if v is None:
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def _contexts(root):
    """context id -> (period_end, kind, axis, member_or_key).

    kind is 'doc' | 'category' | 'holder' | 'sbo'.
    """
    out = {}
    for ctx in root.iter():
        if _local(ctx.tag) != "context":
            continue
        cid = ctx.get("id")
        if not cid:
            continue
        end = None
        for p in ctx.iter():
            lp = _local(p.tag)
            if lp in ("instant", "endDate"):
                end = (p.text or "").strip() or end
        kind, axis, key = "doc", None, None
        for m in ctx.iter():
            lm = _local(m.tag)
            if lm == "explicitMember":
                axis = _local(m.get("dimension", ""))
                if axis == "CategoryOfShareholdersAxis":
                    kind, key = "category", _local((m.text or "").strip())
            elif lm == "typedMember":
                axis = _local(m.get("dimension", ""))
                # the typed value is the child element's text
                val = next((_clean(c.text) for c in list(m)), None)
                key = val or cid
                kind = "sbo" if axis == "SignificantBeneficialOwnersAxis" else "holder"
        out[cid] = (end, kind, axis, key)
    return out


def _bucket(axis: str | None) -> str | None:
    """'DetailsOfSharesHeldByOthersIndianShareholdersAxis' -> 'OthersIndianShareholders'."""
    if not axis:
        return None
    a = re.sub(r"^Details(OfThe|Of)?(Shares)?(HeldBy)?", "", axis)
    return re.sub(r"Axis$", "", a) or None


def parse(xml_text: str) -> dict:
    """Return {meta, categories, holders, sbo} for one shareholding filing."""
    root = ET.fromstring(xml_text.encode("utf-8", "replace")
                         if isinstance(xml_text, str) else xml_text)
    ctx = _contexts(root)

    meta: dict = {}
    cats: dict = {}
    holders: dict = {}
    sbos: dict = {}
    period_end = None

    for el in root.iter():
        cref = el.get("contextRef")
        if not cref:
            continue
        name = _local(el.tag)
        end, kind, axis, key = ctx.get(cref, (None, "doc", None, None))
        if end and (period_end is None or end > period_end):
            period_end = end

        if kind == "doc":
            if name in META:
                meta.setdefault(META[name], _clean(el.text))
            continue

        if kind == "sbo":
            row = sbos.setdefault(key, {"key": key})
            if name in SBO:
                row[SBO[name]] = _clean(el.text)
            continue

        target = cats.setdefault(key, {"category": key}) if kind == "category" \
            else holders.setdefault(key, {"key": key, "bucket": _bucket(axis)})

        if name in NUM:
            v = _num(el.text)
            if v is not None:
                target[NUM[name]] = v
        elif name in TXT:
            v = _clean(el.text)
            if v is not None:
                target[TXT[name]] = v
            else:
                # Masked ('******') or empty. Record the key as None so a
                # caller can tell "NSE withheld this" from "tag absent", and
                # so h["promoter_type"] never raises. A real value later on
                # the same context still wins.
                target.setdefault(TXT[name], None)

    tax = _TAX.search(xml_text[:4000] if isinstance(xml_text, str) else "")
    meta["taxonomy"] = tax.group(1) if tax else None
    meta["quarter_end"] = period_end

    # SCALE. The 2016-era taxonomy writes percentages as percentages
    # (promoter 63.60 -> "6360.00"), the 2025-era ones write fractions
    # (promoter 50.48% -> "0.5048"). Same tag, same meaning, 10,000x apart.
    # Anchor on the one identity that must hold in every filing:
    # promoter + public + non-promoter-non-public = the whole company. That
    # sum lands near 1, near 100, or near 10000, which tells us the scale
    # without trusting the taxonomy date. Everything is stored as PERCENT.
    whole = sum(cats.get(k, {}).get("pct") or 0 for k in (
        "ShareholdingOfPromoterAndPromoterGroupMember",
        "PublicShareholdingMember",
        "SharesHeldByNonPromoterNonPublicShareholdersMember"))
    factor = 1.0
    if 0 < whole <= 5:                 # fractions: 1.0
        factor = 100.0
    elif whole > 500:                  # already x100: 10000
        factor = 0.01
    meta["pct_scale"] = whole or None
    meta["pct_factor"] = factor
    if factor != 1.0:
        pcts = ("pct", "pct_voting", "pct_diluted", "locked_in_pct", "enc_pct")
        for row in list(cats.values()) + list(holders.values()):
            for k in pcts:
                if row.get(k) is not None:
                    row[k] = row[k] * factor

    # A filer on the new taxonomy may report the three parts and omit the sum.
    # Recompute rather than leave a NULL that reads as "no encumbrance".
    for row in list(cats.values()) + list(holders.values()):
        parts = [row.get(k) for k in ("enc_pledged", "enc_ndu", "enc_other")]
        if row.get("enc_total") is None and any(p is not None for p in parts):
            row["enc_total"] = sum(p for p in parts if p is not None)

    return {
        "meta": meta,
        "categories": sorted(cats.values(), key=lambda r: r["category"] or ""),
        "holders": [h for h in holders.values() if h.get("name")],
        "sbo": [s for s in sbos.values() if s.get("sbo_name")],
    }
