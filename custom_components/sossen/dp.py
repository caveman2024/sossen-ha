"""SOSSEN register (DP) map.

Pure constants with no Home Assistant dependency, so the protocol codec
can be imported (and tested) without Home Assistant installed.
"""

# DP IDs for reading (0x1000+ range)
DP_STATUS = 4096
DP_ENERGY_TOTAL = 4098
DP_AC_VOLTAGE = 4103
DP_AC_POWER = 4126
DP_AC_FREQUENCY = 4131
DP_DC_CURRENT_1 = 4145
DP_DC_VOLTAGE_1 = 4146
DP_DC_POWER_1 = 4147
DP_DC_CURRENT_2 = 4149
DP_DC_VOLTAGE_2 = 4150
DP_DC_POWER_2 = 4151
# Datapoints 4152 and 4156 are numeric gaps in the otherwise regular
# channel sequence; their function is unknown, so they are left unmapped.
# Channels 3/4 (4in1 models only) — mapping confirmed independently on
# 4in1 2400W hardware by both community forks (flrs-94, Loudramin).
DP_DC_CURRENT_3 = 4153
DP_DC_VOLTAGE_3 = 4154
DP_DC_POWER_3 = 4155
DP_DC_CURRENT_4 = 4157
DP_DC_VOLTAGE_4 = 4158
DP_DC_POWER_4 = 4159
DP_DC_POWER_TOTAL = 4169
# Lifetime energy counters (identified by correlation, not WiFi/RSSI as an
# older revision assumed): 4172 increments once per ~0.205 kWh of AC energy
# produced — it grows only during production, is frozen at night, and its
# absolute value matches the fine odometer DP 4098 (e.g. 4900 * 0.205 =
# 1004 kWh = the app's "total production"). 4174 is the same on the DC/input
# side (~5% higher), so 4172/4174 is the lifetime conversion efficiency, the
# signal for a slowly-degrading microinverter. Both are 16-bit and monotonic.
DP_ENERGY_AC_UNITS = 4172
DP_ENERGY_DC_UNITS = 4174
DP_TEMPERATURE = 4183

# DP IDs for writing (0x8000+ range)
DP_SET_FLAG = 32770
DP_SET_POWER_LIMIT = 32771

# Tuya DP numbers
TUYA_DP_COMMAND = 24
TUYA_DP_POLL = [4103]
