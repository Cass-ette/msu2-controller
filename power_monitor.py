"""Power data contract and background sampling; no Tk or serial access."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math
import plistlib
import subprocess
import sys
import threading
import time


@dataclass(frozen=True)
class PowerSample:
    timestamp: float
    system_w: float | None = None
    input_w: float | None = None
    # Positive means energy enters the battery; negative means discharge.
    battery_w: float | None = None
    percent: float | None = None
    external: bool | None = None
    installed: bool | None = None
    state: str = '等待采样'
    adapter_rating_w: float | None = None
    health_percent: float | None = None
    cycles: int | None = None
    remaining_minutes: int | None = None
    system_source: str = ''
    input_source: str = ''
    battery_source: str = ''
    note: str = ''
    error: str = ''
    system_pending: bool = False
    sensor_age_seconds: float | None = None


def number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        return None
    return float(value)


def signed(value):
    """IORegistry may encode negative currents/powers as unsigned 32/64 bit."""
    if isinstance(value, int) and not isinstance(value, bool):
        if 2**63 <= value < 2**64:
            return value - 2**64
        if 2**31 <= value < 2**32:
            return value - 2**32
    return value


def ratio(current, maximum):
    current, maximum = number(current, 0), number(maximum, 0)
    if current is None or not maximum:
        return None
    return min(100., current / maximum * 100)


def parse_mac_battery(item, now=None):
    """AppleSmartBattery values: voltage mV, current mA, telemetry power mW.

    AdapterDetails describes a negotiated capacity, never the measured input.
    Battery-terminal estimates are explicitly labelled and no SoC-only sum is
    presented as whole-machine consumption. Undocumented keys may be absent.
    """
    now = time.monotonic() if now is None else now
    telemetry = item.get('PowerTelemetryData')
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    data = item.get('BatteryData')
    data = data if isinstance(data, dict) else {}
    adapter = item.get('AdapterDetails')
    adapter = adapter if isinstance(adapter, dict) else {}
    external = item.get('ExternalConnected')
    external = bool(external) if isinstance(external, (bool, int)) else None
    installed = item.get('BatteryInstalled')
    installed = bool(installed) if isinstance(installed, (bool, int)) else None

    volts = number(item.get('Voltage'), 1, 30000)
    amps = number(signed(item.get('InstantAmperage')), -100000, 100000)
    if amps is None:
        amps = number(signed(item.get('Amperage')), -100000, 100000)
    battery = volts * amps / 1e6 if volts is not None and amps is not None else None
    battery_source = '电池电压 × 电流（估算）' if battery is not None else ''
    raw_battery = number(signed(telemetry.get('BatteryPower')), -1000000, 1000000)
    raw_input = number(telemetry.get('SystemPowerIn'), 0, 1000000)
    # During unplugging the registry can briefly retain the last AC telemetry.
    stale_ac = (external is False and raw_input is not None and raw_input > 0
                and (raw_battery is None or raw_battery > 0))
    if raw_battery is not None and not stale_ac:
        candidate = raw_battery / 1000
        # Prefer the battery sensor for direction if charging telemetry lags.
        if (battery is None or (external is False and candidate < 0)
                or (candidate * battery >= 0 and abs(candidate-battery) <= max(3, abs(battery)*.25))):
            battery, battery_source = candidate, '系统电池功率遥测'
    # AC presence changes before the cached current does. A disconnected Mac
    # cannot charge its battery from the removed adapter: hide that stale sign.
    pending_current = external is False and battery is not None and battery > .1
    if pending_current or installed is False:
        battery, battery_source = None, ''

    input_w = raw_input / 1000 if raw_input is not None else None
    input_source = '系统输入功率遥测' if input_w is not None else ''
    if external is False:
        input_w, input_source = 0., '未接外部电源'
    elif input_w is None:
        voltage = number(telemetry.get('SystemVoltageIn'), 0, 100000)
        current = number(telemetry.get('SystemCurrentIn'), 0, 100000)
        if voltage is not None and current is not None:
            input_w, input_source = voltage * current / 1e6, '输入电压 × 电流（估算）'

    system = number(telemetry.get('SystemLoad'), 0, 1000000)
    system = system / 1000 if system is not None else None
    system_source = '整机负载遥测（不含电池充电）' if system is not None else ''
    system_pending = bool(system is not None and (stale_ac or pending_current))
    if system_pending:
        system_source = '整机负载遥测（切换前读数，等待刷新）'
    if external is False and battery is not None and battery <= 0 and (stale_ac or system is None):
        system, system_pending = -battery, False
        system_source = '电池端放电估算（含供电转换损耗）'
    if system is None and input_w is not None and battery is not None:
        balance = input_w - battery
        if balance >= 0:
            system = balance
            system_source = ('电池端放电估算（含供电转换损耗）' if external is False
                             else '输入 − 电池功率（收支估算）')

    if installed is False:
        state = '无电池'
    elif pending_current:
        state = '放电 · 等待刷新'
    elif battery is not None and battery < -.1:
        state = '混合供电' if external else '放电'
    elif external and (item.get('IsCharging') or (battery is not None and battery > .1)):
        state = '充电'
    elif item.get('FullyCharged'):
        state = '已充满'
    elif external:
        state = '接电未充电'
    else:
        state = '电池供电' if external is False else '状态未知'
    remaining = item.get('AvgTimeToFull' if state == '充电' else 'AvgTimeToEmpty')
    if remaining is None:
        remaining = item.get('TimeRemaining')
    remaining = number(remaining, 0, 65534)
    if state not in ('充电', '放电', '电池供电'):
        remaining = None
    health = ratio(data.get('FullChargeCapacity', data.get('AppleRawMaxCapacity')),
                   data.get('DesignCapacity', item.get('DesignCapacity')))
    cycles = number(item.get('CycleCount'), 0, 100000)
    note = 'Mac 系统遥测；输入是电脑端功率，不是插座功率。'
    if system_pending:
        note = '电源切换中：整机暂示上次遥测；等待系统刷新，电池旧充电读数已隐藏。'
    if system is None:
        note += ' 此机型未提供整机功率。'
    if input_w is None:
        note += ' 实际输入不可用；额定功率不代替实测。'
    updated = number(item.get('FullPathUpdated', item.get('UpdateTime')), 1)
    sensor_age = max(0., time.time()-updated) if updated is not None else None
    return PowerSample(now, system, input_w, battery,
                       ratio(item.get('CurrentCapacity'), item.get('MaxCapacity')),
                       external, installed, state,
                       number(adapter.get('Watts'), 0, 1000) if external else None,
                       health, int(cycles) if cycles is not None else None,
                       int(remaining) if remaining is not None else None,
                       system_source, input_source, battery_source, note,
                       system_pending=system_pending,sensor_age_seconds=sensor_age)


def read_mac_power():
    result = subprocess.run(['/usr/sbin/ioreg', '-r', '-c', 'AppleSmartBattery', '-a'],
                            capture_output=True, timeout=1.5, check=True)
    items = plistlib.loads(result.stdout)
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return PowerSample(time.monotonic(), state='无电池遥测',
                           note='此 Mac 未提供 AppleSmartBattery；整机与输入功率不可用。')
    return parse_mac_battery(items[0])


def read_basic_battery():
    import psutil
    battery = psutil.sensors_battery()
    if battery is None:
        return PowerSample(time.monotonic(), installed=False, state='无电池',
                           note='当前平台未提供整机与输入功率。')
    remaining = battery.secsleft / 60 if battery.secsleft >= 0 else None
    return PowerSample(time.monotonic(), percent=number(battery.percent, 0, 100),
                       external=battery.power_plugged, installed=True,
                       state='接电' if battery.power_plugged else '放电',
                       remaining_minutes=int(remaining) if remaining is not None else None,
                       note='当前平台仅提供电量与接电状态，功率不可用。')


class PowerMonitor:
    """Lazy, independent collector: slow OS queries never block USB or Tk."""
    def __init__(self, reader=None, interval=2., clock=time.monotonic):
        self.reader = reader or (read_mac_power if sys.platform == 'darwin' else read_basic_battery)
        self.interval, self.clock = interval, clock
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.latest = PowerSample(clock())
        self.history = deque(maxlen=150)

    def start(self):
        with self.lock:
            if self.thread is not None or self.stop_event.is_set():
                return
            self.thread = threading.Thread(target=self._loop, name='msu2-power', daemon=True)
            self.thread.start()

    def collect(self):
        try:
            sample = self.reader()
        except Exception as exc:
            # Never preserve old watts after a failed query or unplug transition.
            detail = str(exc).strip()[:160]
            sample = PowerSample(self.clock(), state='采集失败', error=detail,
                                 note='电源数据暂不可用；不影响小屏连接。')
        with self.lock:
            self.latest = sample
            self.history.append(sample)
        return sample

    def _loop(self):
        while not self.stop_event.is_set():
            self.collect()
            if self.stop_event.wait(self.interval):
                break

    def snapshot(self):
        self.start()
        with self.lock:
            sample = self.latest
        if self.clock() - sample.timestamp > max(6., self.interval*3):
            return replace(sample, system_w=None, input_w=None, battery_w=None,
                           percent=None, external=None, state='数据已过期',
                           error='采集延迟或系统刚从睡眠恢复，请等待新样本。')
        return sample

    def samples(self):
        with self.lock:
            return tuple(self.history)

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.)


def watts(value, signed_battery=False):
    if value is None:
        return '-- W'
    return f'{value:+.1f} W' if signed_battery else f'{value:.1f} W'


def energy_routes(sample):
    """Directional topology; widths are deliberately not proportional to watts."""
    routes = []
    if sample.external and sample.input_w is not None and sample.input_w > .1:
        routes.append(('adapter', 'system'))
    if sample.battery_w is not None and sample.battery_w > .1 and sample.external:
        routes.append(('adapter', 'battery'))
    elif ((sample.battery_w is not None and sample.battery_w < -.1)
          or (sample.external is False and sample.installed is True)):
        routes.append(('battery', 'system'))
    return tuple(routes)
