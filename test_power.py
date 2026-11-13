"""Power accounting, missing telemetry, collector isolation and shared graphics."""
from dataclasses import replace
import plistlib
from types import SimpleNamespace
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

import app
import power_monitor as power
from power_graphics import draw_trend, render_dashboard, render_power


def charging():
    # Units and fields observed on the development Mac, with rounded values.
    return {'Voltage':12500,'Amperage':3600,'CurrentCapacity':74,'MaxCapacity':100,
            'IsCharging':True,'ExternalConnected':True,'BatteryInstalled':True,
            'CycleCount':164,'AvgTimeToFull':53,
            'AdapterDetails':{'Watts':100,'AdapterVoltage':20000,'Current':5000},
            'BatteryData':{'FullChargeCapacity':5343,'DesignCapacity':6075},
            'PowerTelemetryData':{'SystemLoad':19000,'SystemPowerIn':64000,'BatteryPower':45000}}


class MacPowerTests(unittest.TestCase):
    def test_charging_measured_input_is_not_adapter_rating(self):
        sample = power.parse_mac_battery(charging(),10)
        self.assertEqual((sample.system_w,sample.input_w,sample.battery_w),(19,64,45))
        self.assertEqual(sample.input_w-sample.battery_w,sample.system_w)
        self.assertEqual(sample.adapter_rating_w,100)
        self.assertEqual((sample.percent,sample.state,sample.remaining_minutes),(74,'充电',53))
        self.assertAlmostEqual(sample.health_percent,87.95,places=2)
        self.assertEqual(power.energy_routes(sample),(('adapter','system'),('adapter','battery')))

    def test_battery_only_uses_signed_32_and_64_bit_current(self):
        for current in (-1600,2**32-1600,2**64-1600):
            item=charging()
            item.update(Amperage=current,IsCharging=False,ExternalConnected=False,
                        AvgTimeToEmpty=120)
            item['PowerTelemetryData']={}
            sample=power.parse_mac_battery(item,10)
            self.assertEqual((sample.battery_w,sample.system_w,sample.input_w),(-20,20,0))
            self.assertIn('估算',sample.system_source)
            self.assertEqual(sample.state,'放电')
            self.assertIsNone(sample.adapter_rating_w)
            self.assertEqual(power.energy_routes(sample),(('battery','system'),))

    def test_unplug_transition_discards_stale_ac_telemetry(self):
        item=charging()
        item.update(Amperage=-1000,ExternalConnected=False,IsCharging=False)
        sample=power.parse_mac_battery(item)
        self.assertEqual((sample.input_w,sample.battery_w,sample.system_w),(0,-12.5,12.5))
        self.assertNotEqual(sample.system_source,'整机负载遥测（不含电池充电）')

    def test_unplug_with_old_positive_current_keeps_labelled_last_system_reading(self):
        item=charging()
        item.update(ExternalConnected=False,IsCharging=False)
        sample=power.parse_mac_battery(item)
        self.assertIsNone(sample.battery_w)
        self.assertEqual(sample.system_w,19)
        self.assertEqual(sample.input_w,0)
        self.assertTrue(sample.system_pending)
        self.assertIn('等待刷新',sample.system_source)
        self.assertIn('等待刷新',sample.state)
        self.assertEqual(power.energy_routes(sample),(('battery','system'),))

    def test_missing_instant_current_uses_average_and_exposes_sensor_age(self):
        item=charging()
        item.update(InstantAmperage=None,FullPathUpdated=100)
        item['PowerTelemetryData']={}
        with patch.object(power.time,'time',return_value=140):
            sample=power.parse_mac_battery(item)
        self.assertEqual(sample.battery_w,45)
        self.assertEqual(sample.sensor_age_seconds,40)

    def test_actual_discharge_telemetry_is_used_even_with_residual_input(self):
        item=charging()
        item.update(ExternalConnected=False,IsCharging=False,Voltage=12227,Amperage=-2390)
        item['PowerTelemetryData'].update(SystemLoad=30130,SystemPowerIn=908,BatteryPower=-29222)
        sample=power.parse_mac_battery(item)
        self.assertEqual((sample.input_w,sample.battery_w,sample.system_w),(0,-29.222,30.13))
        self.assertFalse(sample.system_pending)
        self.assertIn('遥测',sample.system_source)
        # Some fields update before others; a negative telemetry power is valid
        # when the disconnected battery's average current is still positive.
        item['Amperage']=1000
        self.assertEqual(power.parse_mac_battery(item).battery_w,-29.222)

    def test_hybrid_power_does_not_infer_charging_from_ac_presence(self):
        item=charging()
        item.update(Amperage=-800,IsCharging=False)
        item['PowerTelemetryData'].update(SystemLoad=25000,SystemPowerIn=15000,BatteryPower=2**64-10000)
        sample=power.parse_mac_battery(item)
        self.assertEqual((sample.state,sample.battery_w),('混合供电',-10))
        self.assertEqual(power.energy_routes(sample),(('adapter','system'),('battery','system')))

    def test_mismatched_battery_power_prefers_current_direction(self):
        item=charging()
        item['Amperage']=-800
        sample=power.parse_mac_battery(item)
        self.assertEqual(sample.battery_w,-10)
        self.assertIn('估算',sample.battery_source)

    def test_missing_input_never_uses_negotiated_voltage_or_rating(self):
        item=charging()
        item['PowerTelemetryData']={}
        sample=power.parse_mac_battery(item)
        self.assertIsNone(sample.system_w)
        self.assertIsNone(sample.input_w)
        self.assertEqual(sample.adapter_rating_w,100)
        self.assertIn('额定功率不代替实测',sample.note)

    def test_input_vi_fallback_and_power_balance_are_labelled_estimates(self):
        item=charging()
        item['PowerTelemetryData']={'SystemVoltageIn':20000,'SystemCurrentIn':3200}
        sample=power.parse_mac_battery(item)
        self.assertEqual((sample.input_w,sample.battery_w,sample.system_w),(64,45,19))
        self.assertIn('估算',sample.input_source)
        self.assertIn('估算',sample.system_source)
        item['PowerTelemetryData']['SystemCurrentIn']=100
        self.assertIsNone(power.parse_mac_battery(item).system_w)

    def test_percentage_legacy_capacity_and_invalid_numbers(self):
        item=charging()
        item.update(CurrentCapacity=3000,MaxCapacity=6000,AvgTimeToFull=65535,
                    Voltage=float('nan'),Amperage=100,CycleCount=True)
        item['PowerTelemetryData']={'SystemLoad':float('inf'),'BatteryPower':False}
        sample=power.parse_mac_battery(item)
        self.assertEqual(sample.percent,50)
        self.assertIsNone(sample.system_w)
        self.assertIsNone(sample.battery_w)
        self.assertIsNone(sample.cycles)
        self.assertIsNone(sample.remaining_minutes)

    def test_idle_battery_full_and_unknown_data(self):
        item=charging()
        item.update(IsCharging=False,FullyCharged=True,Amperage=0)
        item['PowerTelemetryData']['BatteryPower']=0
        sample=power.parse_mac_battery(item)
        self.assertEqual(sample.state,'已充满')
        self.assertIsNone(sample.remaining_minutes)
        self.assertEqual(power.energy_routes(sample),(('adapter','system'),))
        empty=power.parse_mac_battery({})
        self.assertEqual(empty.state,'状态未知')
        self.assertIsNone(empty.system_w)
        self.assertIsNone(empty.input_w)
        self.assertIsNone(empty.percent)
        self.assertEqual(power.energy_routes(empty),())

    def test_mac_reader_has_timeout_and_parses_plist(self):
        result=SimpleNamespace(stdout=plistlib.dumps([charging()]))
        with patch.object(power.subprocess,'run',return_value=result) as run:
            sample=power.read_mac_power()
        self.assertEqual(sample.input_w,64)
        self.assertEqual(run.call_args.args[0],['/usr/sbin/ioreg','-r','-c','AppleSmartBattery','-a'])
        self.assertEqual(run.call_args.kwargs['timeout'],1.5)
        with patch.object(power.subprocess,'run',return_value=SimpleNamespace(stdout=plistlib.dumps([]))):
            self.assertIsNone(power.read_mac_power().system_w)

    def test_other_platform_reports_battery_without_fabricating_watts(self):
        import psutil
        with patch.object(psutil,'sensors_battery',return_value=SimpleNamespace(percent=60,power_plugged=False,secsleft=3600)), \
             patch.object(power.sys,'platform','win32'), \
             patch.object(power.subprocess,'run') as run:
            monitor=power.PowerMonitor()
            sample=monitor.collect()
        run.assert_not_called()
        self.assertEqual((sample.percent,sample.state,sample.remaining_minutes),(60,'放电',60))
        self.assertIsNone(sample.system_w)
        self.assertIsNone(sample.input_w)
        self.assertIsNone(sample.battery_w)
        monitor.close()


class CollectorTests(unittest.TestCase):
    def test_query_is_lazy_and_never_blocks_snapshot_or_serial_worker(self):
        started,release=threading.Event(),threading.Event()
        threads=[]
        def reader():
            threads.append(threading.current_thread().name)
            started.set()
            release.wait(2)
            return power.PowerSample(time.monotonic(),system_w=12)
        monitor=power.PowerMonitor(reader=reader)
        self.addCleanup(monitor.close)
        self.assertIsNone(monitor.thread)
        start=time.monotonic()
        monitor.snapshot()
        self.assertLess(time.monotonic()-start,.2)
        self.assertTrue(started.wait(1))
        release.set()
        monitor.close()
        self.assertEqual(threads,['msu2-power'])
        self.assertFalse(monitor.thread.is_alive())

    def test_failure_clears_previous_watts_and_history_is_bounded(self):
        with patch.object(power,'read_mac_power') as reader:
            reader.side_effect=[power.PowerSample(10,system_w=12),subprocess.TimeoutExpired('ioreg',1.5)]
            monitor=power.PowerMonitor(reader=reader,clock=lambda:12)
            self.assertEqual(monitor.collect().system_w,12)
            failed=monitor.collect()
            self.assertIsNone(failed.system_w)
            self.assertTrue(failed.error)
            self.assertIsNone(monitor.samples()[-1].system_w)
        monitor.reader=lambda:power.PowerSample(20)
        for _ in range(160):
            monitor.collect()
        self.assertEqual(len(monitor.samples()),150)
        monitor.close()

    def test_sleep_or_delayed_collector_cannot_present_old_power_as_current(self):
        monitor=power.PowerMonitor(reader=lambda:power.PowerSample(10,system_w=99),clock=lambda:30)
        monitor.collect()
        monitor.stop_event.set()  # Inspect stale state without starting another query.
        sample=monitor.snapshot()
        self.assertIsNone(sample.system_w)
        self.assertIsNone(sample.percent)
        self.assertEqual(sample.state,'数据已过期')
        self.assertEqual(power.energy_routes(sample),())
        monitor.close()


class GraphicsTests(unittest.TestCase):
    def test_graphics_use_real_font_and_all_power_states(self):
        base=power.parse_mac_battery(charging(),100)
        for sample in (base,replace(base,battery_w=-10,state='混合供电'),
                       power.PowerSample(100,state='不可用',error='读取失败')):
            frame=render_power((160,80),sample,app.font)
            self.assertEqual(frame.size,(160,80))
            large=render_dashboard(sample,[sample],app.font,100)
            self.assertEqual(large.size,(900,650))
            self.assertGreater(len(set(frame.tobytes())),10)

    def test_trend_uses_time_axis_negative_power_and_breaks_missing_samples(self):
        class Draw:
            def __init__(self): self.lines=[]
            def line(self,coords,**kwargs): self.lines.append((coords,kwargs))
            def text(self,*args,**kwargs): pass
            def ellipse(self,*args,**kwargs): pass
        samples=[power.PowerSample(t,battery_w=w) for t,w in
                 ((10,-10),(12,-20),(14,None),(16,-10),(30,-10),(32,-10))]
        draw=Draw()
        draw_trend(draw,(0,0,300,100),samples,app.font,310)
        battery_lines=[line for line,kw in draw.lines if kw.get('fill')=='#fbbf24']
        self.assertEqual(len(battery_lines),2)
        self.assertEqual(battery_lines[0][0][0],0)
        self.assertEqual(battery_lines[0][1][0],2)
        self.assertEqual(battery_lines[1][0][0],20)
        self.assertNotEqual(battery_lines[0][0][1],battery_lines[0][1][1])


if __name__=='__main__':
    unittest.main(verbosity=2)
