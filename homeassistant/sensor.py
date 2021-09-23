"""Calculates comfort values from temperature and humidity."""
import logging
import math

import voluptuous as vol

from homeassistant import util
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_SENSORS,
    DEVICE_CLASS_HUMIDITY,
    DEVICE_CLASS_TEMPERATURE,
    EVENT_HOMEASSISTANT_START,
    PERCENTAGE,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)

ATTR_HUMIDITY = "humidity"

CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_SENSOR_TYPES = "sensor_types"
CONF_FRIENDLY_NAME = "friendly_name"

DEFAULT_NAME = "Comfort"

GRAMS_PER_CUBIC_METER = "g/m³"

SENSOR_TYPES = {
    "absolutehumidity": "Absolute Humidity",
    "heatindex": "Heat Index",
    "dewpoint": "Dew Point",
    "perception": "Thermal Perception",
    "simmerindex": "Summer Simmer Index",
    "simmerzone": "Summer Simmer Index Danger",
}

DEFAULT_SENSOR_TYPES = SENSOR_TYPES.keys()

SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Required(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_SENSOR_TYPES, default=DEFAULT_SENSOR_TYPES): cv.ensure_list,
        vol.Optional(CONF_FRIENDLY_NAME): cv.string,
        #        vol.Optional(CONF_ICON_TEMPLATE): cv.template,
        #        vol.Optional(CONF_ENTITY_PICTURE_TEMPLATE): cv.template,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_SENSORS): cv.schema_with_slug_keys(SENSOR_SCHEMA),
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up comfort sensor."""

    sensors = []

    for name, device_config in config[CONF_SENSORS].items():
        temperature_sensor = device_config.get(CONF_TEMPERATURE_SENSOR)
        humidity_sensor = device_config.get(CONF_HUMIDITY_SENSOR)
        config_sensor_types = device_config.get(CONF_SENSOR_TYPES)
        friendly_name = device_config.get(CONF_FRIENDLY_NAME, name)
        #        icon_template = device_config.get(CONF_ICON_TEMPLATE)
        #        entity_picture_template = device_config.get(CONF_ENTITY_PICTURE_TEMPLATE)

        for sensor_type in config_sensor_types:
            if sensor_type in SENSOR_TYPES:
                sensors.append(
                    ComfortSensor(
                        name,
                        friendly_name,
                        temperature_sensor,
                        humidity_sensor,
                        #                        icon_template,
                        #                        entity_picture_template,
                        sensor_type,
                    )
                )
    if not sensors:
        _LOGGER.error("No sensors added")
        return False

    async_add_entities(sensors, False)


class ComfortSensor(SensorEntity):
    """Represents a comfort sensor."""

    def __init__(
        self,
        name,
        friendly_name,
        temp_sensor,
        humidity_sensor,
        sensor_type,
    ):
        """Initialize the sensor."""
        self._name = name
        self._friendly_name = friendly_name
        self._temp_sensor = temp_sensor
        self._humidity_sensor = humidity_sensor
        self._sensor_type = sensor_type

        self._available = False
        self._sensors = [self._temp_sensor, self._humidity_sensor]
        self._state = None
        self._temp_c = None
        self._humidity = None

    async def async_added_to_hass(self):
        """Register callbacks."""

        @callback
        def comfort_sensors_state_listener(event):
            """Handle for state changes for dependent sensors."""
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            entity = event.data.get("entity_id")
            _LOGGER.debug(
                f"Sensor state change for {entity} that had old state {old_state} and new state {new_state}",
            )

            if self._update_sensor(entity, old_state, new_state):
                self.async_schedule_update_ha_state(True)

        @callback
        def comfort_startup(event):
            """Add listeners and get 1st state."""
            _LOGGER.debug(f"Startup for {self.entity_id}")

            async_track_state_change_event(
                self.hass, self._sensors, comfort_sensors_state_listener
            )

            # Read initial state
            indoor_temp = self.hass.states.get(self._temp_sensor)
            indoor_hum = self.hass.states.get(self._humidity_sensor)

            schedule_update = self._update_sensor(self._temp_sensor, None, indoor_temp)

            schedule_update = (
                False
                if not self._update_sensor(self._humidity_sensor, None, indoor_hum)
                else schedule_update
            )

            if schedule_update:
                self.async_schedule_update_ha_state(True)

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, comfort_startup)

    def _update_sensor(self, entity, old_state, new_state):
        """Update information based on new sensor states."""
        _LOGGER.debug(f"Sensor update for {entity}")
        if new_state is None:
            return False

        # If old_state is not set and new state is unknown then it means
        # that the sensor just started up
        if old_state is None and new_state.state == STATE_UNKNOWN:
            return False

        if entity == self._temp_sensor:
            self._temp_c = ComfortSensor._get_temp_from_state(new_state)
        elif entity == self._humidity_sensor:
            self._humidity = ComfortSensor._get_humidity_from_state(new_state)

        return True

    @staticmethod
    def _get_temp_from_state(state):
        """Parse temperature sensor value."""
        _LOGGER.debug(f"Updating temp sensor with value {state.state}")

        # Return an error if the sensor change its state to Unknown.
        if state.state == STATE_UNKNOWN:
            _LOGGER.error(
                f"Unable to parse temperature sensor {state.entity_id} with state: {state.state}",
            )
            return None

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        temp = util.convert(state.state, float)

        if temp is None:
            _LOGGER.error(
                f"Unable to parse temperature sensor {state.entity_id} with state: {state.state}",
            )
            return None

        # convert to celsius if necessary
        if unit == TEMP_FAHRENHEIT:
            return util.temperature.fahrenheit_to_celsius(temp)
        if unit == TEMP_CELSIUS:
            return temp
        _LOGGER.error(
            f"Temp sensor {state.entity_id} has unsupported unit: {unit} (allowed: {TEMP_CELSIUS}, {TEMP_FAHRENHEIT})",
        )

        return None

    @staticmethod
    def _get_humidity_from_state(state):
        """Parse humidity sensor value."""
        _LOGGER.debug(f"Updating humidity sensor with value {state.state}")

        # Return an error if the sensor change its state to Unknown.
        if state.state == STATE_UNKNOWN:
            _LOGGER.error(
                f"Unable to parse humidity sensor {state.entity_id}, state: {state.state}",
            )
            return None

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        hum = util.convert(state.state, float)

        if hum is None:
            _LOGGER.error(
                f"Unable to parse humidity sensor {state.entity_id}, state: {state.state}",
            )
            return None

        if unit != PERCENTAGE:
            _LOGGER.error(
                f"Humidity sensor {state.entity_id} has unsupported unit: {unit} (allowed: {PERCENTAGE})",
            )
            return None

        if hum > 100 or hum < 0:
            _LOGGER.error(
                f"Humidity sensor {state.entity_id} is out of range: {hum} (allowed: 0-100%)",
            )
            return None

        return hum

    async def async_update(self):
        """Calculate latest state."""
        _LOGGER.debug(f"Update state for {self.entity_id}")
        # check all sensors
        if None in (self._temp_c, self._humidity):
            self._available = False
            return

        if self._sensor_type == "dewpoint":
            self._state = self._calc_dew_point()
        elif self._sensor_type == "heatindex":
            self._state = self._calc_heat_index()
        elif self._sensor_type == "absolutehumidity":
            self._state = self._calc_absolute_humidity()
        elif self._sensor_type == "perception":
            self._state = self._calc_thermal_perception()
        elif self._sensor_type == "simmerindex":
            self._state = self._calc_simmer_index()
        elif self._sensor_type == "simmerdanger":
            self._state = self._calc_simmer_danger()

        self._available = self._state is not None

    def _calc_dew_point(self):
        """Calculate the dew point."""

        temp_c = self._temp_c
        humidity = self._humidity

        # https://en.wikipedia.org/wiki/Arden_Buck_equation
        if temp_c < 0:
            saturation_vapor_pressure = 6.1115 * math.exp(
                (23.036 - (temp_c / 333.7)) * (temp_c / (279.82 + temp_c))
            )
        else:
            saturation_vapor_pressure = 6.1121 * math.exp(
                (18.678 - (temp_c / 234.5)) * (temp_c / (257.14 + temp_c))
            )
        vapor_pressure = saturation_vapor_pressure * (humidity / 100.0)
        dew_point = (-430.22 + 237.7 * math.log(vapor_pressure)) / (
            -math.log(vapor_pressure) + 19.08
        )
        return dew_point

    def _calc_thermal_perception(self):
        """Calculate thermal perception value."""

        # https://en.wikipedia.org/wiki/Dew_point
        dew_point = self._calc_dew_point()
        if dew_point < 10:
            return "A bit dry for some"
        if dew_point < 13:
            return "Very comfortable"
        if dew_point < 16:
            return "Comfortable"
        if dew_point < 18:
            return "OK for most"
        if dew_point < 21:
            return "Somewhat uncomfortable"
        if dew_point < 24:
            return "Very humid, quite uncomfortable"
        if dew_point < 26:
            return "Extremely uncomfortable"
        return "Severely high"

    def _calc_heat_index(self):
        """Calculate the heat index."""

        temp_f = util.temperature.celsius_to_fahrenheit(self._temp_c)
        humidity = self._humidity

        # http://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml
        heat_index = 0.5 * (
            temp_f + 61.0 + ((temp_f - 68.0) * 1.2) + (humidity * 0.094)
        )

        if heat_index > 79:
            heat_index = -42.379 + 2.04901523 * temp_f
            heat_index += 10.14333127 * humidity
            heat_index += -0.22475541 * temp_f * humidity
            heat_index += -0.00683783 * pow(temp_f, 2)
            heat_index += -0.05481717 * pow(humidity, 2)
            heat_index += 0.00122874 * pow(temp_f, 2) * humidity
            heat_index += 0.00085282 * temp_f * pow(humidity, 2)
            heat_index += -0.00000199 * pow(temp_f, 2) * pow(humidity, 2)

        if humidity < 13 and temp_f >= 80 and temp_f <= 112:
            heat_index = heat_index - ((13 - humidity) * 0.25) * math.sqrt(
                (17 - abs(temp_f - 95)) * 0.05882
            )
        elif humidity > 85 and temp_f >= 80 and temp_f <= 87:
            heat_index += ((humidity - 85) * 0.1) * ((87 - temp_f) * 0.2)

        return util.temperature.fahrenheit_to_celsius(heat_index)

    def _calc_absolute_humidity(self):
        """Calculate absolute humidity."""

        temp_c = self._temp_c
        humidity = self._humidity

        # https://carnotcycle.wordpress.com/2012/08/04/how-to-convert-relative-humidity-to-absolute-humidity/
        abs_humidity = 6.112
        abs_humidity *= math.exp((17.67 * temp_c) / (243.5 + temp_c))
        abs_humidity *= humidity
        abs_humidity *= 2.1674
        abs_humidity /= temp_c + 273.15

        return abs_humidity

    def _calc_simmer_index(self):
        """Calculate simmer index."""

        temp_f = util.temperature.celsius_to_fahrenheit(self._temp_c)
        humidity = self._humidity

        # https://www.vcalc.com/wiki/rklarsen/Summer+Simmer+Index
        if temp_f < 70:
            simmer_index = temp_f
        else:
            simmer_index = (
                1.98 * (temp_f - (0.55 - (0.0055 * humidity)) * (temp_f - 58.0)) - 56.83
            )

        return util.temperature.fahrenheit_to_celsius(simmer_index)

    def _calc_simmer_danger(self):
        """Calculate simmer index zone."""

        simmer_index = self._calc_simmer_index()
        if simmer_index < 21.1:
            return None
        if simmer_index < 25.0:
            return "Slightly cool"
        if simmer_index < 28.3:
            return "Comfortable"
        if simmer_index < 32.8:
            return "Slightly warm"
        if simmer_index < 37.8:
            return "Increasing discomfort"
        if simmer_index < 44.4:
            return "Extremely warm"
        if simmer_index < 51.7:
            return "Danger of heatstroke"
        if simmer_index < 65.6:
            return "Extreme danger of heatstroke"
        return "Circulatory collapse imminent"

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        if self._sensor_type in ("dewpoint", "heatindex", "simmerindex"):
            return DEVICE_CLASS_TEMPERATURE
        if self._sensor_type == "absolutehumidity":
            return DEVICE_CLASS_HUMIDITY
        return None

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        if self._sensor_type in ("dewpoint", "heatindex", "simmerindex"):
            return self.hass.config.units.temperature_unit
        if self._sensor_type == "absolutehumidity":
            return GRAMS_PER_CUBIC_METER
        return None

    @property
    def native_value(self):
        """Return the state of the entity."""
        return self._state

    @property
    def available(self):
        """Return the availability of this sensor."""
        return self._available

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if self.hass.config.units.is_metric:
            return {
                ATTR_TEMPERATURE: round(self._temp_c, 2),
                ATTR_HUMIDITY: round(self._humidity, 2),
            }

        temp_f = util.temperature.celsius_to_fahrenheit(self._temp_c)
        return {
            ATTR_TEMPERATURE: round(temp_f, 2),
            ATTR_HUMIDITY: round(self._humidity, 2),
        }
