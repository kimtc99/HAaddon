import paho.mqtt.client as mqtt
import json
import time
import asyncio
from selenium import webdriver

share_dir = '/share'
config_dir = '/data'
data_dir = '/pycommax'

HA_TOPIC = 'homenet'
STATE_TOPIC = HA_TOPIC + '/{}/{}/state'
ELFIN_TOPIC = 'ew11'
ELFIN_SEND_TOPIC = ELFIN_TOPIC + '/send'


def log(string):
    date = time.strftime('%Y-%m-%d %p %I:%M:%S', time.localtime(time.time()+9*60*60))
    print('[{}] {}'.format(date, string))
    return


def checksum(input_hex):
    try:
        input_hex = input_hex[:14]
        s1 = sum([int(input_hex[val], 16) for val in range(0, 14, 2)])
        s2 = sum([int(input_hex[val + 1], 16) for val in range(0, 14, 2)])
        s1 = s1 + int(s2 // 16)
        s1 = s1 % 16
        s2 = s2 % 16
        return input_hex + format(s1, 'X') + format(s2, 'X')
    except:
        return None


def find_device(config):
    with open(data_dir + '/commax_devinfo.json') as file:
        dev_info = json.load(file)
    statePrefix = {dev_info[name]['stateON'][:2]: name for name in dev_info if dev_info[name].get('stateON')}
    device_num = {statePrefix[prefix]: 0 for prefix in statePrefix}
    collect_data = {statePrefix[prefix]: set() for prefix in statePrefix}

    target_time = time.time() + 360

    def on_connect(client, userdata, flags, rc):
        userdata = time.time() + 360
        if rc == 0:
            log("Connected to MQTT broker..")
            log("Find devices for 360s..")
            client.subscribe('ew11/#', 0)
        else:
            errcode = {1: 'Connection refused - incorrect protocol version',
                       2: 'Connection refused - invalid client identifier',
                       3: 'Connection refused - server unavailable',
                       4: 'Connection refused - bad username or password',
                       5: 'Connection refused - not authorised'}
            log(errcode[rc])

    def on_message(client, userdata, msg):
        raw_data = msg.payload.hex().upper()
        for k in range(0, len(raw_data), 16):
            data = raw_data[k:k + 16]
            # log(data)
            if data == checksum(data) and data[:2] in statePrefix:
                name = statePrefix[data[:2]]
                collect_data[name].add(data)
                if dev_info[name].get('stateNUM'):
                    device_num[name] = max([device_num[name], int(data[int(dev_info[name]['stateNUM']) - 1])])
                else:
                    device_num[name] = 1

    mqtt_client = mqtt.Client('mqtt2elfin-commax')
    mqtt_client.username_pw_set(config['mqtt_id'], config['mqtt_password'])
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect_async(config['mqtt_server'])
    mqtt_client.user_data_set(target_time)
    mqtt_client.loop_start()

    while time.time() < target_time:
        pass

    mqtt_client.loop_stop()

    log('다음의 데이터를 찾았습니다...')
    log('======================================')

    for name in collect_data:
        collect_data[name] = sorted(collect_data[name])
        dev_info[name]['Number'] = device_num[name]
        log('DEVICE: {}'.format(name))
        log('Packets: {}'.format(collect_data[name]))
        log('-------------------')
    log('======================================')
    log('기기의 숫자만 변경하였습니다. 상태 패킷은 직접 수정하여야 합니다.')
    with open(share_dir + '/commax_found_device.json', 'w', encoding='utf-8') as make_file:
        json.dump(dev_info, make_file, indent="\t")
        log('기기리스트 저장 중 : /share/commax_found_device.json')
    return dev_info


def do_work(config, device_list):
    debug = config['DEBUG']
    mqtt_log = config['mqtt_log']
    elfin_log = config['elfin_log']
    find_signal = config['save_unregistered_signal']

    def pad(value):
        value = int(value)
        return '0' + str(value) if value < 10 else str(value)

    def make_hex(k, input_hex, change):
        if input_hex:
            try:
                change = int(change)
                input_hex = '{}{}{}'.format(input_hex[:change - 1], int(input_hex[change - 1]) + k, input_hex[change:])
            except:
                pass
        return checksum(input_hex)

    def make_hex_temp(k, curTemp, setTemp, state):  # 온도조절기 16자리 (8byte) hex 만들기
        if state == 'OFF' or state == 'ON' or state == 'CHANGE':
            tmp_hex = device_list['Thermo'].get('command' + state)
            change = device_list['Thermo'].get('commandNUM')
            tmp_hex = make_hex(k, tmp_hex, change)
            if state == 'CHANGE':
                setT = pad(setTemp)
                chaTnum = OPTION['Thermo'].get('chaTemp')
                tmp_hex = tmp_hex[:chaTnum - 1] + setT + tmp_hex[chaTnum + 1:]
            return checksum(tmp_hex)
        else:
            tmp_hex = device_list['Thermo'].get(state)
            change = device_list['Thermo'].get('stateNUM')
            tmp_hex = make_hex(k, tmp_hex, change)
            setT = pad(setTemp)
            curT = pad(curTemp)
            curTnum = OPTION['Thermo'].get('curTemp')
            setTnum = OPTION['Thermo'].get('setTemp')
            tmp_hex = tmp_hex[:setTnum - 1] + setT + tmp_hex[setTnum + 1:]
            tmp_hex = tmp_hex[:curTnum - 1] + curT + tmp_hex[curTnum + 1:]
            if state == 'stateOFF':
                return checksum(tmp_hex)
            elif state == 'stateON':
                tmp_hex2 = tmp_hex[:3] + str(3) + tmp_hex[4:]
                return [checksum(tmp_hex), checksum(tmp_hex2)]
            else:
                return None

    def make_device_info(dev_name, device):
        num = device.get('Number', 0)
        if num > 0:
            arr = {k + 1: {cmd + onoff: make_hex(k, device.get(cmd + onoff), device.get(cmd + 'NUM'))
                           for cmd in ['command', 'state'] for onoff in ['ON', 'OFF']} for k in range(num)}
            if dev_name == 'Fan':
                tmp_hex = arr[1]['stateON']
                change = device_list['Fan'].get('speedNUM')
                arr[1]['stateON'] = [make_hex(k, tmp_hex, change) for k in range(3)]
                tmp_hex = device_list['Fan'].get('commandCHANGE')
                arr[1]['CHANGE'] = [make_hex(k, tmp_hex, change) for k in range(3)]

            arr['Num'] = num
            return arr
        else:
            return None

    DEVICE_LISTS = {}
    for name in device_list:
        device_info = make_device_info(name, device_list[name])
        if device_info:
            DEVICE_LISTS[name] = device_info

    prefix_list = {}
    log('----------------------')
    log('등록된 기기 목록 DEVICE_LISTS..')
    log('----------------------')
    for name in DEVICE_LISTS:
        state = DEVICE_LISTS[name][1].get('stateON')
        if state:
            prefix = state[0][:2] if isinstance(state, list) else state[:2]
            prefix_list[prefix] = name
        log('{}: {}'.format(name, DEVICE_LISTS[name]))
    log('----------------------')

    HOMESTATE = {}
    QUEUE = []
    COLLECTDATA = {'cond': find_signal, 'data': set(), 'EVtime': time.time(), 'LastRecv': time.time_ns()}
    if find_signal:
        log('[LOG] 50개의 신호를 수집 중..')

    async def recv_from_HA(topics, value):
        device = topics[1][:-1]
        if mqtt_log:
            log('[LOG] HA ->> : {} -> {}'.format('/'.join(topics), value))

        if device in DEVICE_LISTS:
            key = topics[1] + topics[2]
            idx = int(topics[1][-1])
            cur_state = HOMESTATE.get(key)
            value = 'ON' if value == 'heat' else value.upper()
            if cur_state:
                if value == cur_state:
                    if debug:
                        log('[DEBUG] {} is already set: {}'.format(key, value))
                else:
                    if device == 'Thermo':
                        curTemp = HOMESTATE.get(topics[1] + 'curTemp')
                        setTemp = HOMESTATE.get(topics[1] + 'setTemp')
                        if topics[2] == 'power':
                            sendcmd = make_hex_temp(idx - 1, curTemp, setTemp, value)
                            recvcmd = [make_hex_temp(idx - 1, curTemp, setTemp, 'state' + value)]
                            if sendcmd:
                                QUEUE.append({'sendcmd': sendcmd, 'recvcmd': recvcmd, 'count': 0})
                                if debug:
                                    log('[DEBUG] Queued ::: sendcmd: {}, recvcmd: {}'.format(sendcmd, recvcmd))
                        elif topics[2] == 'setTemp':
                            value = int(float(value))
                            if value == int(setTemp):
                                if debug:
                                    log('[DEBUG] {} is already set: {}'.format(topics[1], value))
                            else:
                                setTemp = value
                                sendcmd = make_hex_temp(idx - 1, curTemp, setTemp, 'CHANGE')
                                recvcmd = [make_hex_temp(idx - 1, curTemp, setTemp, 'stateON')]
                                if sendcmd:
                                    QUEUE.append({'sendcmd': sendcmd, 'recvcmd': recvcmd, 'count': 0})
                                    if debug:
                                        log('[DEBUG] Queued ::: sendcmd: {}, recvcmd: {}'.format(sendcmd, recvcmd))

                    elif device == 'Fan':
                        if topics[2] == 'power':
                            sendcmd = DEVICE_LISTS[device][idx].get('command' + value)
                            recvcmd = DEVICE_LISTS[device][idx].get('state' + value) if value == 'ON' else [
                                DEVICE_LISTS[device][idx].get('state' + value)]
                            QUEUE.append({'sendcmd': sendcmd, 'recvcmd': recvcmd, 'count': 0})
                            if debug:
                                log('[DEBUG] Queued ::: sendcmd: {}, recvcmd: {}'.format(sendcmd, recvcmd))
                        elif topics[2] == 'speed':
                            speed_list = ['LOW', 'MEDIUM', 'HIGH']
                            if value in speed_list:
                                index = speed_list.index(value)
                                sendcmd = DEVICE_LISTS[device][idx]['CHANGE'][index]
                                recvcmd = [DEVICE_LISTS[device][idx]['stateON'][index]]
                                QUEUE.append({'sendcmd': sendcmd, 'recvcmd': recvcmd, 'count': 0})
                                if debug:
                                    log('[DEBUG] Queued ::: sendcmd: {}, recvcmd: {}'.format(sendcmd, recvcmd))

                    else:
                        sendcmd = DEVICE_LISTS[device][idx].get('command' + value)
                        if sendcmd:
                            recvcmd = [DEVICE_LISTS[device][idx].get('state' + value, 'NULL')]
                            QUEUE.append({'sendcmd': sendcmd, 'recvcmd': recvcmd, 'count': 0})
                            if debug:
                                log('[DEBUG] Queued ::: sendcmd: {}, recvcmd: {}'.format(sendcmd, recvcmd))
                        else:
                            if debug:
                                log('[DEBUG] There is no command for {}'.format('/'.join(topics)))
            else:
                if debug:
                    log('[DEBUG] There is no command about {}'.format('/'.join(topics)))
        else:
            if debug:
                log('[DEBUG] There is no command for {}'.format('/'.join(topics)))

    async def slice_raw_data(raw_data):
        if elfin_log:
            log('[SIGNAL] receved: {}'.format(raw_data))
        # if COLLECTDATA['cond']:
        #     if len(COLLECTDATA['data']) < 50:
        #         if data not in COLLECTDATA['data']:
        #             COLLECTDATA['data'].add(data)
        #     else:
        #         COLLECTDATA['cond'] = False
        #         with open(share_dir + '/collected_signal.txt', 'w', encoding='utf-8') as make_file:
        #             json.dump(COLLECTDATA['data'], make_file, indent="\t")
        #             log('[Complete] Collect 50 signals. See : /share/collected_signal.txt')
        #         COLLECTDATA['data'] = None

        cors = [recv_from_elfin(raw_data[k:k + 16]) for k in range(0, len(raw_data), 16) if raw_data[k:k + 16] == checksum(raw_data[k:k + 16])]
        await asyncio.gather(*cors)

    async def recv_from_elfin(data):
        COLLECTDATA['LastRecv'] = time.time_ns()
        if data:
            if HOMESTATE.get('EV1power') == 'ON':
                if COLLECTDATA['EVtime'] < time.time():
                    await update_state('EV', 0, 'OFF')
            for que in QUEUE:
                if data in que['recvcmd']:
                    QUEUE.remove(que)
                    if debug:
                        log('[DEBUG] Found matched hex: {}. Delete a queue: {}'.format(raw_data, que))
                    break

            device_name = prefix_list.get(data[:2])
            if device_name == 'Thermo':
                curTnum = device_list['Thermo']['curTemp']
                setTnum = device_list['Thermo']['setTemp']
                curT = data[curTnum - 1:curTnum + 1]
                setT = data[setTnum - 1:setTnum + 1]
                onoffNUM = device_list['Thermo']['stateONOFFNUM']
                staNUM = device_list['Thermo']['stateNUM']
                index = int(data[staNUM - 1]) - 1
                onoff = 'ON' if int(data[onoffNUM - 1]) > 0 else 'OFF'
                await update_state(device_name, index, onoff)
                await update_temperature(index, curT, setT)
            elif device_name == 'Fan':
                if data in DEVICE_LISTS['Fan'][1]['stateON']:
                    speed = DEVICE_LISTS['Fan'][1]['stateON'].index(data)
                    await update_state('Fan', 0, 'ON')
                    await update_fan(0, speed)
                elif data == DEVICE_LISTS['Fan'][1]['stateOFF']:
                    await update_state('Fan', 0, 'OFF')
                else:
                    log("[WARNING] <{}> 기기의 신호를 찾음: {}".format(device_name, data))
                    log('[WARNING] 기기목록에 등록되지 않는 패킷입니다. JSON 파일을 확인하세요..')
            elif device_name == 'Outlet':
                staNUM = device_list['Outlet']['stateNUM']
                index = int(data[staNUM - 1]) - 1

                for onoff in ['OFF', 'ON']:
                    if data.startswith(DEVICE_LISTS[device_name][index + 1]['state' + onoff][:8]):
                        await update_state(device_name, index, onoff)
                        if onoff == 'ON':
                            await update_outlet_value(index, data[10:14])
                        else:
                            await update_outlet_value(index, 0)
            elif device_name == 'EV':
                val = int(data[4:6], 16)
                await update_state('EV', 0, 'ON')
                await update_ev_value(0, val)
                COLLECTDATA['EVtime'] = time.time() + 3
            else:
                num = DEVICE_LISTS[device_name]['Num']
                state = [DEVICE_LISTS[device_name][k + 1]['stateOFF'] for k in range(num)] + [
                    DEVICE_LISTS[device_name][k + 1]['stateON'] for k in range(num)]
                if data in state:
                    index = state.index(data)
                    onoff, index = ['OFF', index] if index < num else ['ON', index - num]
                    await update_state(device_name, index, onoff)
                else:
                    log("[WARNING] <{}> 기기의 신호를 찾음: {}".format(device_name, data))
                    log('[WARNING] 기기목록에 등록되지 않는 패킷입니다. JSON 파일을 확인하세요..')

    async def update_state(device, idx, onoff):
        state = 'power'
        deviceID = device + str(idx + 1)
        key = deviceID + state

        if onoff != HOMESTATE.get(key):
            HOMESTATE[key] = onoff
            topic = STATE_TOPIC.format(deviceID, state)
            mqtt_client.publish(topic, onoff.encode())
            if mqtt_log:
                log('[LOG] ->> HA : {} >> {}'.format(topic, onoff))
        else:
            if debug:
                log('[DEBUG] {} is already set: {}'.format(deviceID, onoff))
        return

    async def update_fan(idx, onoff):
        deviceID = 'Fan' + str(idx + 1)
        if onoff == 'ON' or onoff == 'OFF':
            state = 'power'

        else:
            try:
                speed_list = ['low', 'medium', 'high']
                onoff = speed_list[int(onoff) - 1]
                state = 'speed'
            except:
                return
        key = deviceID + state

        if onoff != HOMESTATE.get(key):
            HOMESTATE[key] = onoff
            topic = STATE_TOPIC.format(deviceID, state)
            mqtt_client.publish(topic, onoff.encode())
            if mqtt_log:
                log('[LOG] ->> HA : {} >> {}'.format(topic, onoff))
        else:
            if debug:
                log('[DEBUG] {} is already set: {}'.format(deviceID, onoff))
        return

    async def update_temperature(idx, curTemp, setTemp):
        deviceID = 'Thermo' + str(idx + 1)
        temperature = {'curTemp': pad(curTemp), 'setTemp': pad(setTemp)}
        for state in temperature:
            key = deviceID + state
            val = temperature[state]
            if val != HOMESTATE.get(key):
                HOMESTATE[key] = val
                topic = STATE_TOPIC.format(deviceID, state)
                mqtt_client.publish(topic, val.encode())
                if mqtt_log:
                    log('[LOG] ->> HA : {} -> {}'.format(topic, val))
            else:
                if debug:
                    log('[DEBUG] {} is already set: {}'.format(key, val))
        return

    async def update_outlet_value(idx, val):
        deviceID = 'Outlet' + str(idx + 1)
        try:
            val = '%.1f' % float(int(val) / 10)
            topic = STATE_TOPIC.format(deviceID, 'watt')
            mqtt_client.publish(topic, val.encode())
            if debug:
                log('[LOG] ->> HA : {} -> {}'.format(topic, val))
        except:
            pass

    async def update_ev_value(idx, val):
        deviceID = 'EV' + str(idx + 1)
        try:
            BF = device_info['EV']['BasementFloor']
            val = str(int(val) - BF + 1) if val >= BF else 'B' + str(BF - int(val))
            topic = STATE_TOPIC.format(deviceID, 'floor')
            mqtt_client.publish(topic, val.encode())
            if debug:
                log('[LOG] ->> HA : {} -> {}'.format(topic, val))
        except:
            pass

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            log("MQTT 접속 중..")
            client.subscribe([(HA_TOPIC + '/#', 0), (ELFIN_TOPIC + '/recv', 0), (ELFIN_TOPIC + '/send', 1)])
            if 'EV' in DEVICE_LISTS:
                asyncio.run(update_state('EV', 0, 'OFF'))
        else:
            errcode = {1: 'Connection refused - incorrect protocol version',
                       2: 'Connection refused - invalid client identifier',
                       3: 'Connection refused - server unavailable',
                       4: 'Connection refused - bad username or password',
                       5: 'Connection refused - not authorised'}
            log(errcode[rc])

    def on_message(client, userdata, msg):
        topics = msg.topic.split('/')
        try:
            if topics[0] == HA_TOPIC and topics[-1] == 'command':
                asyncio.run(recv_from_HA(topics, msg.payload.decode('utf-8')))
            elif topics[0] == ELFIN_TOPIC and topics[-1] == 'recv':
                asyncio.run(slice_raw_data(msg.payload.hex().upper()))
        except:
            pass

    mqtt_client = mqtt.Client('mqtt2elfin-commax')
    mqtt_client.username_pw_set(config['mqtt_id'], config['mqtt_password'])
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect_async(config['mqtt_server'])
    # mqtt_client.user_data_set(target_time)
    mqtt_client.loop_start()

    async def send_to_elfin():
        while True:
            try:
                if time.time_ns() - COLLECTDATA['LastRecv'] > 10000000000:  # 10s
                    log('[WARNING] 10초간 신호를 받지 못했습니다. ew11 기기를 재시작합니다.')
                    try:
                        elfin_id = config['elfin_id']
                        elfin_password = config['elfin_password']
                        elfin_server = config['elfin_server']

                        url = 'http://{}:{}@{}/others.html'.format(elfin_id, elfin_password, elfin_server)

                        options = webdriver.ChromeOptions()
                        options.add_argument('--no-sandbox')
                        options.add_argument('--headless')
                        options.add_argument('--disable-gpu')
                        driver = webdriver.Chrome(options=options)
                        driver.get(url=url)
                        button = driver.find_element_by_xpath('//*[@id="restart"]')
                        button.click()

                        alert = driver.switch_to.alert
                        print(alert.text)
                        alert.accept()

                        await asyncio.sleep(10)
                    except:
                        log('[WARNING] 기기 재시작 오류! 기기 상태를 확인하세요.')
                    COLLECTDATA['LastRecv'] = time.time_ns()
                elif time.time_ns() - COLLECTDATA['LastRecv'] > 100000000:
                    if QUEUE:
                        send_data = QUEUE.pop(0)
                        if elfin_log:
                            log('[SIGNAL] 신호 전송: {}'.format(send_data))
                        mqtt_client.publish(ELFIN_SEND_TOPIC, bytes.fromhex(send_data['sendcmd']))
                        # await asyncio.sleep(0.01)
                        if send_data['count'] < 5:
                            send_data['count'] = send_data['count'] + 1
                            QUEUE.append(send_data)
                        else:
                            if elfin_log:
                                log('[SIGNAL] Send over 5 times. Send Failure. Delete a queue: {}'.format(send_data))
            except Exception as err:
                log('[ERROR] send_to_elfin(): {}'.format(err))
                return True
            await asyncio.sleep(0.01)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_to_elfin())
    loop.close()
    mqtt_client.loop_stop()


if __name__ == '__main__':
    with open(config_dir + '/options.json') as file:
        CONFIG = json.load(file)
    try:
        with open(share_dir + '/commax_found_device.json') as file:
            log('기기 정보 파일을 찾음: /share/commax_found_device.json')
            OPTION = json.load(file)
    except IOError:
        log('기기 정보 파일이 없습니다.: /share/commax_found_device.json')
        OPTION = find_device(CONFIG)
    while True:
        do_work(CONFIG, OPTION)
