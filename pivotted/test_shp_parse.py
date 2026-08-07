"""Tests for shp_parse — the traps, not the happy path.

    pivot/.venv/bin/python -m pytest pivotted/test_shp_parse.py -q

Every case here is something a real filing actually does. The scale case is
the one that matters most: it is silent, it is 100x, and it only shows up on
history, so nothing about a spot check of the latest quarter would catch it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shp_parse  # noqa: E402

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:in-bse-shp="http://www.bseindia.com/xbrl/shp/{tax}/in-bse-shp">
"""


def ctx_cat(cid, member, instant="2026-06-30"):
    return f"""<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier
 scheme="s">1</xbrli:identifier></xbrli:entity><xbrli:period>
 <xbrli:instant>{instant}</xbrli:instant></xbrli:period><xbrli:scenario>
 <xbrldi:explicitMember dimension="in-bse-shp:CategoryOfShareholdersAxis"
 >in-bse-shp:{member}</xbrldi:explicitMember></xbrli:scenario></xbrli:context>"""


def ctx_holder(cid, axis, key, instant=True):
    period = ("<xbrli:instant>2026-06-30</xbrli:instant>" if instant else
              "<xbrli:startDate>2026-04-01</xbrli:startDate>"
              "<xbrli:endDate>2026-06-30</xbrli:endDate>")
    return f"""<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier
 scheme="s">1</xbrli:identifier></xbrli:entity><xbrli:period>{period}
 </xbrli:period><xbrli:scenario><xbrldi:typedMember
 dimension="in-bse-shp:{axis}"><in-bse-shp:D>{key}</in-bse-shp:D>
 </xbrldi:typedMember></xbrli:scenario></xbrli:context>"""


def fact(tag, cid, val):
    return f'<in-bse-shp:{tag} contextRef="{cid}">{val}</in-bse-shp:{tag}>'


def build(tax, promoter_pct, public_pct, extra=""):
    return (HEAD.format(tax=tax)
            + ctx_cat("P", "ShareholdingOfPromoterAndPromoterGroupMember")
            + ctx_cat("U", "PublicShareholdingMember")
            + fact("ShareholdingAsAPercentageOfTotalNumberOfShares", "P", promoter_pct)
            + fact("NumberOfShares", "P", "1000")
            + fact("ShareholdingAsAPercentageOfTotalNumberOfShares", "U", public_pct)
            + extra + "</xbrli:xbrl>")


def _cat(doc, member):
    return next(c for c in doc["categories"] if c["category"] == member)


class TestScale:
    """2016 files write 6360.00 for 63.60%; 2025 files write 0.5048 for 50.48%."""

    def test_fraction_era_scales_up(self):
        doc = shp_parse.parse(build("2025-10-31", "0.5048", "0.4952"))
        assert round(_cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["pct"], 2) == 50.48
        assert round(_cat(doc, "PublicShareholdingMember")["pct"], 2) == 49.52

    def test_percent_era_scales_down(self):
        doc = shp_parse.parse(build("2016-06-23", "6360.00", "3640.00"))
        assert round(_cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["pct"], 2) == 63.60
        assert round(_cat(doc, "PublicShareholdingMember")["pct"], 2) == 36.40

    def test_already_percent_is_left_alone(self):
        doc = shp_parse.parse(build("2022-09-30", "63.60", "36.40"))
        assert round(_cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["pct"], 2) == 63.60

    def test_scale_is_decided_by_the_sum_not_the_taxonomy_date(self):
        # A filer on a new taxonomy who writes whole percentages must not be
        # multiplied by 100. The identity promoter+public=100 is the anchor.
        doc = shp_parse.parse(build("2025-10-31", "63.60", "36.40"))
        assert round(_cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["pct"], 2) == 63.60


class TestEncumbrance:
    """Vedanta files pledged=false and 2.03bn shares under 'other'."""

    def test_total_is_recomputed_when_filer_omits_it(self):
        doc = shp_parse.parse(build(
            "2025-10-31", "0.5472", "0.4512",
            extra=fact("NumberOfSharesEncumberedUnderOtherEncumbrances", "P", "600")
                  + fact("NumberOfSharesEncumberedUnderPledged", "P", "0")))
        assert _cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["enc_total"] == 600

    def test_explicit_total_is_not_double_counted(self):
        doc = shp_parse.parse(build(
            "2025-10-31", "0.5472", "0.4512",
            extra=fact("NumberOfSharesEncumbered", "P", "700")
                  + fact("NumberOfSharesEncumberedUnderPledged", "P", "700")))
        assert _cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["enc_total"] == 700

    def test_old_taxonomy_single_number_maps_to_the_same_column(self):
        doc = shp_parse.parse(build(
            "2019-06-30", "63.60", "36.40",
            extra=fact("PledgedOrEncumberedNumberOfShares", "P", "450")))
        assert _cat(doc, "ShareholdingOfPromoterAndPromoterGroupMember")["enc_total"] == 450


class TestHolders:
    def test_name_and_numbers_merge_across_the_two_contexts(self):
        # Name lives on the duration context, numbers on the instant one;
        # they share only the typed key.
        extra = (ctx_holder("D_x", "DetailsOfSharesHeldByOthersIndianShareholdersAxis",
                            "OthersIndian_15", instant=False)
                 + ctx_holder("I_x", "DetailsOfSharesHeldByOthersIndianShareholdersAxis",
                              "OthersIndian_15", instant=True)
                 + fact("NameOfTheShareholder", "D_x", "Srichakra Commercials LLP")
                 + fact("TypeOfPromoterShareholding", "D_x", "Promoter Group")
                 + fact("NumberOfShares", "I_x", "1479199658"))
        doc = shp_parse.parse(build("2025-10-31", "0.5048", "0.4952", extra))
        assert len(doc["holders"]) == 1
        h = doc["holders"][0]
        assert h["name"] == "Srichakra Commercials LLP"
        assert h["promoter_type"] == "Promoter Group"
        assert h["shares"] == 1479199658
        assert h["bucket"] == "OthersIndianShareholders"

    def test_nse_masking_becomes_none_not_asterisks(self):
        extra = (ctx_holder("D_y", "DetailsSharesHeldByIndividualsOrHUFAxis",
                            "IndHUF_1", instant=False)
                 + fact("NameOfTheShareholder", "D_y", "Mukesh D Ambani")
                 + fact("TypeOfPromoterShareholding", "D_y", "******")
                 + fact("PermanentAccountNumberOfShareholder", "D_y", "******"))
        doc = shp_parse.parse(build("2025-10-31", "0.5048", "0.4952", extra))
        assert doc["holders"][0]["promoter_type"] is None

    def test_unnamed_holder_contexts_are_dropped(self):
        extra = (ctx_holder("I_z", "DetailsOfSharesHeldByMutualFundsOrUTIAxis",
                            "MF_1", instant=True)
                 + fact("NumberOfShares", "I_z", "500"))
        doc = shp_parse.parse(build("2025-10-31", "0.5048", "0.4952", extra))
        assert doc["holders"] == []


class TestSBO:
    def test_significant_beneficial_owner_is_its_own_row(self):
        extra = (ctx_holder("S_1", "SignificantBeneficialOwnersAxis", "SBO_1",
                            instant=False)
                 + fact("NameOfSignificantBeneficialOwners", "S_1", "A Person")
                 + fact("NationalityOfSignificantBeneficialOwners", "S_1", "India")
                 + fact("NameOfRegisteredOwner", "S_1", "Some Holdings Pvt Ltd"))
        doc = shp_parse.parse(build("2025-10-31", "0.5048", "0.4952", extra))
        assert doc["holders"] == []          # an SBO is not a shareholder row
        assert doc["sbo"][0]["sbo_name"] == "A Person"
        assert doc["sbo"][0]["registered_owner"] == "Some Holdings Pvt Ltd"


class TestMeta:
    def test_taxonomy_and_quarter_end_are_extracted(self):
        doc = shp_parse.parse(build("2025-10-31", "0.5048", "0.4952"))
        assert doc["meta"]["taxonomy"] == "2025-10-31"
        assert doc["meta"]["quarter_end"] == "2026-06-30"
