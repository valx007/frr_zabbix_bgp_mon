#!/usr/bin/env python
import re
import subprocess
import sys
import json
import argparse
import os
import time

VAL_MAP = {
    "Idle (Admin)": -1,
    "Idle (PfxCt)": -2,
    "Idle": -3,
    "Connect": -4,
    "Active": -5,
    "OpenSent": -6,
    "OpenConfirm": -7,
    "Established": -8
}

JSONFILE = '/tmp/bgpmon.json'
CACHELIFE = 60

parser = argparse.ArgumentParser()
parser.add_argument("action", help="discovery | neighbor_state")
parser.add_argument("-n", help="neighbor")
args = parser.parse_args()

def run_config():
    neighbor_settings = {}
    try:
        process = subprocess.Popen(
            ["vtysh", "-c", "show running-config"], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE
        )
        out, err = process.communicate()
        if process.returncode != 0:
            raise Exception(f"vtysh error: {err.decode().strip()}")
        out = out.decode()
    except Exception as e:
        print(f"ZBX_NOTSUPPORTED: {str(e)}")
        sys.exit(1)
    
    pattern = r'neighbor\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+(description|remote-as|maximum-prefix)\s+(.*)'
    matches = re.findall(pattern, out)
    
    for match in matches:
        ip = match[0]
        key = match[1]
        value = match[2].split('!', 1)[0].strip()
        
        if ip not in neighbor_settings:
            neighbor_settings[ip] = {}
        
        if key in ['remote-as', 'maximum-prefix']:
            try:
                value = int(value)
            except ValueError:
                pass
        
        neighbor_settings[ip][key] = value

    with open(JSONFILE, 'w') as f:
        json.dump({
            "neighbor_settings": neighbor_settings,
            "timestamp": time.time()
        }, f)

    return neighbor_settings

def bgp_summary():
    neighbors = {}
    try:
        process = subprocess.Popen(
            ["vtysh", "-c", "show bgp summary"], 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = process.communicate()
        if process.returncode != 0:
            raise Exception(f"vtysh error: {err.decode().strip()}")
        out = out.decode()
    except Exception as e:
        print(f"ZBX_NOTSUPPORTED: {str(e)}")
        sys.exit(1)
    
    # Парсим вывод построчно
    for line in out.splitlines():
        # Пропускаем служебные строки
        if not line.strip() or 'Neighbor' in line or 'IPv4' in line or 'BGP' in line:
            continue
        
        # Разбиваем строку на колонки
        parts = line.split()
        if len(parts) < 10:
            continue
        
        # Извлекаем IP-адрес
        ip = parts[0]
        
        # Определяем состояние
        # Состояние всегда перед последним числовым столбцом (PfxSnt)
        # Ищем первое нечисловое поле после времени Up/Down
        state_index = 9
        state = ""
        
        # Пытаемся найти начало состояния
        for i in range(9, len(parts)):
            # Если встретили число - это количество префиксов (PfxRcd)
            if parts[i].replace(',', '').isdigit():
                # Если это первый столбец после времени - значит состояние Established
                if i == 9:
                    state = "Established"
                break
            # Проверяем ключевые слова состояний
            if any(parts[i].startswith(s) for s in ["Idle", "Connect", "Active", "Open", "Estab"]):
                state_parts = []
                # Собираем все части состояния
                for j in range(i, len(parts)):
                    # Прерываем при встрече числа (количества префиксов)
                    if parts[j].replace(',', '').isdigit():
                        break
                    state_parts.append(parts[j])
                state = " ".join(state_parts)
                break
        
        # Если состояние не найдено, используем значение по умолчанию
        if not state:
            state = parts[9] if len(parts) > 9 else "Unknown"
        
        neighbors[ip] = state

    with open(JSONFILE, 'w') as f:
        json.dump({
            "neighbors": neighbors,
            "timestamp": time.time()
        }, f)
    
    return neighbors

def get_cached_data():
    if not os.path.exists(JSONFILE):
        return None
        
    try:
        with open(JSONFILE) as f:
            data = json.load(f)
            if time.time() - data.get('timestamp', 0) <= CACHELIFE:
                return data
    except:
        pass
    return None

if __name__ == '__main__':
    result = None
    json_cache = get_cached_data()

    if args.action == 'discovery':
        if not json_cache or 'neighbor_settings' not in json_cache:
            settings = run_config()
        else:
            settings = json_cache['neighbor_settings']
        
        result = {"data": []}
        for ip, config in settings.items():
            result["data"].append({
                "{#PEER_IP}": ip,
                "{#DESCRIPTION}": config.get('description', 'N/A'),
                "{#MAX-PREFIX}": config.get('maximum-prefix', -1)
            })

    elif args.action == 'neighbor_state' and args.n:
        if not json_cache or 'neighbors' not in json_cache:
            states = bgp_summary()
        else:
            states = json_cache['neighbors']
        
        state = states.get(args.n, '')
        # Возвращаем JSON-объект вместо простого числа
        result = {"state": VAL_MAP.get(state, 0)}

    if result is None:
        print("ZBX_NOTSUPPORTED")
        sys.exit(1)

    print(json.dumps(result))
