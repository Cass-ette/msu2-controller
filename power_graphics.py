"""Shared power display graphics, always rendered in upright coordinates."""
import math

from PIL import Image, ImageDraw

from power_monitor import energy_routes, watts

COLORS = {'input_w': '#60a5fa', 'system_w': '#6ee7b7', 'battery_w': '#fbbf24'}
BACKGROUND = '#101824'


def arrow(draw, start, end, color, width=2):
    draw.line((start, end), fill=color, width=width)
    dx, dy = end[0]-start[0], end[1]-start[1]
    angle = math.atan2(dy, dx)
    length = max(4, width*2.5)
    corners = [end]
    for offset in (-.5, .5):
        corners.append((end[0]-length*math.cos(angle+offset),
                        end[1]-length*math.sin(angle+offset)))
    draw.polygon(corners, fill=color)


def render_power(size, sample, font):
    image = Image.new('RGB', size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    sx, sy = size[0]/160, size[1]/80
    scale = min(sx, sy)
    def point(x, y):
        return (round(x*sx), round(y*sy))
    def text(x, y, value, color='white', pixels=10, anchor='mt'):
        draw.text(point(x,y), value, font=font(max(8, round(pixels*scale))),
                  fill=color, anchor=anchor)

    percent = '--%' if sample.percent is None else f'{sample.percent:.0f}%'
    text(3, 1, f'{percent} · {sample.state}', '#bac5d3', 9, 'lt')
    if sample.error:
        text(157, 1, '无实时数据', '#fb7185', 9, 'rt')
    paths = {
        ('adapter','system'): ((50,31),(109,31),COLORS['input_w']),
        ('adapter','battery'): ((28,43),(52,60),COLORS['battery_w']),
        ('battery','system'): ((108,60),(133,44),COLORS['battery_w']),
    }
    for route in energy_routes(sample):
        start, end, color = paths[route]
        arrow(draw, point(*start), point(*end), color, max(1, round(1.5*scale)))
    for label, key, center, box in (
            ('输入','input_w',26,(2,17,50,43)),
            ('整机','system_w',134,(110,17,158,43)),
            ('电池','battery_w',80,(53,51,107,78))):
        color = COLORS[key]
        if key == 'system_w' and sample.system_pending:
            label = '整机·待刷'
        draw.rounded_rectangle((*point(box[0],box[1]),*point(box[2],box[3])),
                               radius=max(2,round(3*scale)), fill='#1b283a', outline=color)
        text(center,box[1]+2,label,color,9)
        value = getattr(sample,key)
        source = getattr(sample,key.replace('_w','_source'))
        prefix = '≈' if '估算' in source and value is not None else ''
        text(center,box[1]+13,prefix+watts(value,key=='battery_w'),pixels=10)
    return image


def draw_trend(draw, box, history, font, now, window=300.):
    left, top, right, bottom = box
    points = [s for s in history if now-window <= s.timestamp <= now]
    values = [getattr(s,key) for s in points for key in COLORS if getattr(s,key) is not None]
    low = min([0.]+values)
    high = max([5.]+values)
    span = max(5., high-low)
    low = math.floor((low-span*.08)/5)*5 if low < 0 else 0
    high = math.ceil((high+span*.08)/5)*5
    for index in range(5):
        value = low+(high-low)*index/4
        y = bottom-(value-low)/(high-low)*(bottom-top)
        draw.line((left,y,right,y),fill='#293549')
        draw.text((left-8,y),f'{value:.0f}',font=font(12),fill='#bac5d3',anchor='rm')
    zero = bottom-(0-low)/(high-low)*(bottom-top)
    draw.line((left,zero,right,zero),fill='#61728a')
    for key,color in COLORS.items():
        previous = None
        for sample in points:
            value = getattr(sample,key)
            if value is None:
                previous = None
                continue
            point = (left+(sample.timestamp-(now-window))/window*(right-left),
                     bottom-(value-low)/(high-low)*(bottom-top))
            if previous and sample.timestamp-previous[0] <= 6:
                draw.line((previous[1],point),fill=color,width=2)
            draw.ellipse((point[0]-1,point[1]-1,point[0]+1,point[1]+1),fill=color)
            previous = (sample.timestamp,point)
    for fraction,label in ((0,'5 分钟前'),(.5,'2.5 分钟前'),(1,'现在')):
        draw.text((left+(right-left)*fraction,bottom+12),label,
                  font=font(12),fill='#bac5d3',anchor='mt')


def render_dashboard(sample, history, font, now):
    image = Image.new('RGB',(900,650),BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((26,18),'功率与电池',font=font(24),fill='white',anchor='lt')
    details = []
    if sample.health_percent is not None:
        details.append(f'容量健康度约 {sample.health_percent:.0f}%')
    if sample.cycles is not None:
        details.append(f'循环 {sample.cycles}')
    if sample.remaining_minutes is not None:
        label = '充满约' if sample.state == '充电' else '剩余约'
        details.append(f'{label} {sample.remaining_minutes} 分钟')
    draw.text((874,30),' · '.join(details),font=font(13),fill='#bac5d3',anchor='rt')
    image.paste(render_power((840,220),sample,font),(30,58))
    draw = ImageDraw.Draw(image)
    rating = '--' if sample.adapter_rating_w is None else watts(sample.adapter_rating_w)
    draw.text((30,290),f'充电器额定 / 协商上限：{rating}   ·   箭头表示方向，宽度不代表功率',
              font=font(13),fill='#bac5d3',anchor='lt')
    for x,key,label in ((50,'system_w','整机'),(190,'input_w','输入'),(330,'battery_w','电池：正充 / 负放')):
        draw.line((x,331,x+18,331),fill=COLORS[key],width=3)
        draw.text((x+25,331),label,font=font(13),fill='white',anchor='lm')
    draw.text((874,331),'最近 5 分钟 · W · 2 秒轮询，系统可能较慢',font=font(12),fill='#bac5d3',anchor='rm')
    draw_trend(draw,(65,365,870,520),history,font,now)
    for y,label,key in ((563,'整机','system_source'),(584,'输入','input_source'),(605,'电池','battery_source')):
        source = getattr(sample,key) or '不可用'
        draw.text((30,y),f'{label}：{source}',font=font(12),fill=COLORS[key.replace('_source','_w')],anchor='lt')
    footer = sample.error or sample.note
    if sample.sensor_age_seconds is not None and not sample.error:
        footer += f' 电池完整采样约 {sample.sensor_age_seconds:.0f} 秒前；功率异步更新。'
    while footer and font(12).getlength(footer) > 840:
        footer = footer[:-2]+'…'
    draw.text((30,630),footer,font=font(12),fill='#fb7185' if sample.error else '#bac5d3',anchor='lt')
    return image
