import cv2, folium, numpy as np, os, time, sqlite3, signal, sys
from threading import Thread
from location import INTERVAL, get_coordinates, get_speed_info, \
    get_driving_distance, get_street_name, get_red_light_camera, detect_turn
from detect import detect
from dotenv import load_dotenv
from roboflow import Roboflow
from database import update_row_database, get_database_length, \
    get_database_value, create_database, create_new_row
from gps_reader import GPSReader
from mqtt_client import ViolationPublisher

REAL_TIME = time.time()

# API
load_dotenv(dotenv_path = "../.env")
API_KEY = Roboflow(os.getenv("ROBOFLOW_API_KEY"))
traffic_project = API_KEY.workspace().project("traffic-light-detection-h8cvg")
traffic_model = traffic_project.version(2).model
stop_project = API_KEY.workspace().project("stop-sign-detection-1")
stop_model = stop_project.version(1).model
turn_project = API_KEY.workspace().project("right-turn-sign-detection")
turn_model = turn_project.version(1).model


# Generate filename based on current date and time
def generate_filename(start_time):
    return str(time.strftime("%m-%d-%Y-%H-%M-%S", start_time)) + '.mp4'

def update_row(tablename, video_timestamp, real_time):
    conn = sqlite3.connect('../data/'+tablename+'.db')
    cursor = conn.cursor()
    cursor.execute(f'''
        UPDATE "{tablename}"
        SET video_timestamp = ?
        WHERE real_time = ?
    ''', (video_timestamp, real_time))
    conn.commit()
    conn.close()


def database_thread(tablename):
    start_time = time.time()
    while True:
        REAL_TIME=time.time()
        create_new_row(tablename, 'real_time', REAL_TIME) # storing real time
        update_row(tablename, round(REAL_TIME-start_time, 2), REAL_TIME)
        time.sleep(INTERVAL)
    
def detect_thread(tablename):
    frame_count = 1
    while True:
        if(os.path.exists('../output_frames/'+tablename+'/'+str(frame_count)+'.png')):
            time.sleep(2)
            detect(tablename, str(frame_count), stop_model, traffic_model, turn_model)
            frame_count+=1
        else:

            continue

def location_thread(tablename, gps_reader, mqtt_publisher):
    load_dotenv(dotenv_path="../.env")
    real_time = generate_filename(time.localtime(REAL_TIME)).replace('-', '_').split('.mp4')[0]
    MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
    paths_path = "../paths/" + tablename + ".txt"
    prev_street, curr_street, turn = "", "", ""
    intersection = False
    prev_lat, prev_long = None, None
    first_coordinate = True

    os.makedirs('../paths/', exist_ok=True)
    
    # Initialize map with GPS position
    lat, lon = gps_reader.get_position()
    if lat and lon:
        map = folium.Map(location=[lat, lon], zoom_start=12)
    else:
        map = folium.Map(location=[0, 0], zoom_start=2)

    stop_sign = False
    stop_sign_lat, stop_sign_long = 0.0, 0.0
    stop_sign_id = -1
    id = -1
    speed, speed_limit, boolean = None, None, None

    while True:
        if(curr_street):
            prev_street = curr_street

        latitude, longitude = get_coordinates(gps_reader, map)
        if latitude is None or longitude is None:
            time.sleep(1)
            continue
        print(latitude, longitude)
        if(get_street_name(MAPS_API_KEY, latitude, longitude) is None):
            continue
        
        if(os.path.isfile(paths_path)):
            speed, speed_limit, boolean = get_speed_info(latitude, longitude, paths_path, real_time)
            first_coordinate = False

        red_light_camera, curr_street = get_red_light_camera(latitude, longitude)
    
        with open(paths_path, 'a') as file:
            file.write(f"{latitude}, {longitude}, Time: {real_time}, Street: {curr_street}\n")
        with open(paths_path, 'r') as file:
            lines = file.readlines()
            id = len([line for line in lines if line.strip() != ''])

        # stop checking for turn after 200m from intersection
        if(intersection and prev_lat and prev_long and get_driving_distance(MAPS_API_KEY, \
            str(latitude)+","+str(longitude), str(prev_lat)+","+str(prev_long))>200): 
            intersection = False

        # detecting intersection
        if(intersection==False and prev_street and prev_street!=curr_street):
            intersection = True
            prev_lat, prev_long = latitude, longitude
        
        if(intersection): # detecting turn
            turn, restriction = detect_turn(latitude, longitude, paths_path, curr_street, MAPS_API_KEY)
            if(restriction!=False and turn == 'right'):
                print('Right Turn Violation')
                if mqtt_publisher:
                    mqtt_publisher.publish_violation(
                        "illegal_right_turn",
                        coordinates=f"{latitude},{longitude}",
                        street=curr_street
                    )

        # detecting rolling stop  
        if(stop_sign==False):
            if(get_database_value(tablename, -1, "stop_sign") and stop_sign_lat==0.0 and stop_sign_long==0.0): # new stop sign detected
                stop_sign = True
                stop_sign_lat, stop_sign_long = latitude, longitude
                stop_sign_id = get_database_length(tablename)
                                                                        
        elif(stop_sign==True):
            if(get_driving_distance(MAPS_API_KEY, str(stop_sign_lat)+','+str(stop_sign_long), str(latitude)+','+str(longitude))<=100):
                # check for stop
                line = ''
                with open(paths_path, 'r') as file:
                    line = file.readlines()[-2]
                if(line.split(', ')[0]==latitude and line.split(', ')[1]==longitude):
                    stop_sign = False
                    stop_sign_id = -1
            else: # didn't stop within 100m after initial detection
                update_row_database("ran_stop_sign", True, stop_sign_id, tablename)
                if mqtt_publisher:
                    mqtt_publisher.publish_violation(
                        "ran_stop_sign",
                        coordinates=f"{latitude},{longitude}",
                        street=curr_street
                    )
                stop_sign = False
                stop_sign_lat, stop_sign_long = 0.0, 0.0
                stop_sign_id = -1
        
        if(stop_sign_lat!=0.0 and stop_sign_long!=0 and get_driving_distance(MAPS_API_KEY, str(stop_sign_lat)+','+str(stop_sign_long), str(latitude)+','+str(longitude))>100):
            stop_sign_lat, stop_sign_long = 0.0, 0.0

        #updating database:
        update_row_database("coordinates", str(latitude)+","+str(longitude), id, tablename)
        update_row_database("street", curr_street, id, tablename)
        if(first_coordinate==False):
            update_row_database("speed", speed, id, tablename)
            update_row_database("speed_limit", speed_limit, id, tablename)
            update_row_database("is_speeding", boolean, id, tablename)
            # Publish speeding violation via MQTT
            if boolean and mqtt_publisher:
                mqtt_publisher.publish_violation(
                    "speeding",
                    coordinates=f"{latitude},{longitude}",
                    street=curr_street,
                    speed=speed,
                    speed_limit=speed_limit
                )
        if(red_light_camera is None):
            red_light_camera = "None"
        update_row_database("red_light_camera", red_light_camera, id, tablename)

        time.sleep(INTERVAL)

def saturate_image(frame):
    hsv_image = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv_image)
    s = cv2.multiply(s, 1.5)
    s = np.clip(s, 0, 255).astype(np.uint8)
    hsv_image = cv2.merge([h, s, v])
    frame = cv2.cvtColor(hsv_image, cv2.COLOR_HSV2BGR)
    return frame

# Main function to capture and record video
def main():
    # --- Initialize GPS ---
    gps_port = os.getenv("GPS_PORT", "/dev/ttyUSB0")
    gps_baudrate = int(os.getenv("GPS_BAUDRATE", "9600"))
    print("[Siren] Initializing GPS...")
    gps = GPSReader(port=gps_port, baudrate=gps_baudrate)
    gps.wait_for_fix(timeout=120)

    # --- Initialize MQTT ---
    mqtt_publisher = None
    mqtt_broker = os.getenv("MQTT_BROKER")
    if mqtt_broker:
        mqtt_publisher = ViolationPublisher(
            broker=mqtt_broker,
            port=os.getenv("MQTT_PORT", "8883"),
            username=os.getenv("MQTT_USERNAME", ""),
            password=os.getenv("MQTT_PASSWORD", "")
        )
        time.sleep(2)  # allow connection to establish
    else:
        print("[MQTT] No MQTT_BROKER set in .env, violations will only be logged locally")

    # --- Initialize Camera ---
    cap = cv2.VideoCapture(0)  # 0 for USB camera
    if not cap.isOpened():
        print("Error: Unable to open video capture.")
        gps.close()
        if mqtt_publisher:
            mqtt_publisher.disconnect()
        exit()

    os.makedirs('../footage/', exist_ok=True)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    filepath = os.path.join('../footage', generate_filename(time.localtime(time.time())).replace('-', '_'))
    fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
    out = cv2.VideoWriter(filepath, fourcc, 20.0, (int(width), int(height)))

    if not out.isOpened():
        print("Error: Unable to open video writer.")
        cap.release()
        gps.close()
        if mqtt_publisher:
            mqtt_publisher.disconnect()
        exit()

    # --- Graceful shutdown on Ctrl+C ---
    running = True
    def signal_handler(sig, frame):
        nonlocal running
        print("\n[Siren] Shutting down...")
        running = False
    signal.signal(signal.SIGINT, signal_handler)

    # --- Start threads ---
    start_time = time.time()
    tablename = generate_filename(time.localtime(start_time)).replace('-', '_').split('.mp4')[0]
    create_database(tablename)
    Thread(target=database_thread, args=(tablename,), daemon=True).start()
    Thread(target=detect_thread, args=(str(tablename),), daemon=True).start()
    Thread(target=location_thread, args=(str(tablename), gps, mqtt_publisher), daemon=True).start()

    frame_path = '../output_frames/'+tablename
    os.makedirs(frame_path, exist_ok=True)
    frame_count = 1

    print(f"[Siren] Recording started. Saving to {filepath}")
    print("[Siren] Press Ctrl+C to stop.")

    while running and cap.isOpened():
        ret, frame = cap.read()

        if ret == True:
            frame = cv2.flip(frame, 1) # remove mirroring

            # Write frame to video file
            out.write(frame)

            # Save frame as PNG every INTERVAL seconds
            current_time = time.time()
            if current_time - start_time >= INTERVAL or frame_count == 1:
                cv2.imwrite(os.path.join(frame_path, f"{frame_count}.png"), saturate_image(frame))
                frame_count += 1
                start_time = current_time
        else:
            time.sleep(0.01)

    # --- Cleanup ---
    print("[Siren] Cleaning up...")
    cap.release()
    out.release()
    gps.close()
    if mqtt_publisher:
        mqtt_publisher.disconnect()
    print(f"[Siren] Saved {frame_count - 1} frames. Video: {filepath}")

if __name__ == "__main__":
    main()