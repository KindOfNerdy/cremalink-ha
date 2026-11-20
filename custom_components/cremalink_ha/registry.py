"""Entity registry housekeeping shared by the platforms."""

from collections.abc import Iterable

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def remove_entities(hass, domain: str, unique_ids: Iterable[str]) -> None:
    """Drop the registry entries of entities this integration no longer creates.

    Renamed or retired entities would otherwise linger as unavailable rows.
    """
    ent_reg = er.async_get(hass)
    for unique_id in unique_ids:
        entity_id = ent_reg.async_get_entity_id(domain, DOMAIN, unique_id)
        if entity_id is not None:
            ent_reg.async_remove(entity_id)
