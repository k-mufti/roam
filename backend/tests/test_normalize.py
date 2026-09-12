from app.ingestion.normalize import (
    GOOGLE_TYPE_MAP,
    distinctive_tokens,
    haversine_m,
    map_category,
    normalize_name,
    synthetic_source_id,
)
from app.models.enums import PlaceCategory


class TestNormalizeName:
    def test_strips_accents_and_case(self):
        assert normalize_name("Café Rivas") == normalize_name("CAFE RIVAS")

    def test_strips_generic_prefix(self):
        assert normalize_name("Restaurante Botín") == "botin"

    def test_keeps_identifying_words(self):
        # "Casa" is load-bearing in Madrid names and must survive.
        assert normalize_name("Casa Lucio") == "casa lucio"

    def test_never_empties_a_name(self):
        # All tokens are noise; the result must still be non-empty.
        assert normalize_name("El Museo") != ""

    def test_cross_language_collapse(self):
        assert normalize_name("Museo del Prado") == "prado"
        assert "prado" in normalize_name("Museo Nacional del Prado")


class TestDistinctiveTokens:
    def test_excludes_generic_tokens(self):
        assert distinctive_tokens(normalize_name("Casa Lucio")) == {"lucio"}

    def test_generic_only_name_has_no_anchor(self):
        shared = distinctive_tokens(normalize_name("Casa Lucio")) & distinctive_tokens(
            normalize_name("Casa Botín")
        )
        assert shared == frozenset()

    def test_short_tokens_excluded(self):
        assert "san" not in distinctive_tokens(normalize_name("Mercado de San Miguel"))
        assert "miguel" in distinctive_tokens(normalize_name("Mercado de San Miguel"))


class TestHaversine:
    def test_known_distance(self):
        # Puerta del Sol -> Museo del Prado is ~1.0 km.
        d = haversine_m(40.41689, -3.70346, 40.41379, -3.69214)
        assert 950 < d < 1100

    def test_zero(self):
        assert haversine_m(40.0, -3.0, 40.0, -3.0) == 0.0


class TestCategoryMapping:
    def test_primary_type_wins_over_table_order(self):
        # Without primary-type precedence this returns ATTRACTION.
        assert (
            map_category(
                ["market", "tourist_attraction", "food"], GOOGLE_TYPE_MAP, primary="market"
            )
            is PlaceCategory.RESTAURANT
        )

    def test_falls_back_to_type_list(self):
        assert map_category(["museum"], GOOGLE_TYPE_MAP) is PlaceCategory.MUSEUM

    def test_unknown_is_other(self):
        assert map_category(["dentist"], GOOGLE_TYPE_MAP) is PlaceCategory.OTHER


def test_synthetic_source_id_is_stable():
    a = synthetic_source_id("reddit", "madrid", "casa dani")
    b = synthetic_source_id("reddit", "madrid", "casa dani")
    assert a == b
    assert a != synthetic_source_id("reddit", "madrid", "casa julio")
