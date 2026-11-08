"""Zuweisung für die Tests. Die App selbst kennt keine festen Entitäten."""
from anbindung.eingaenge import Entitaeten

ENTITAETEN = Entitaeten(
    abgas="sensor.test_abgas",
    druck="sensor.test_druck",
    gradient="sensor.kamin_abbrand_gradient",
    t_max_3min="sensor.kamin_abbrand_t_max_3min",
    tuer="binary_sensor.kamin_abbrand_tuer_offen",
    t_wt="sensor.kamin_t_wt",
    t_wt_anstieg="sensor.kamin_t_wt_anstieg_10min",
    puffer_unten="sensor.test_puffer_unten",
    wartung="input_boolean.kamin_abbrand_wartung",
    handbetrieb="input_boolean.kamin_abbrand_handbetrieb",
    tuerkontakt_vorhanden="input_boolean.kamin_abbrand_tuerkontakt_vorhanden",
)
