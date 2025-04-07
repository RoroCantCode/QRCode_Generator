import os
import time
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify
import cv2
import mediapipe as mp
import requests
import RPi.GPIO as GPIO  # Import Raspberry Pi GPIO library

app = Flask(__name__)

# Initialize Mediapipe
mp_face_detection = mp.solutions.face_detection
mp_hands = mp.solutions.hands
mp_face_mesh = mp.solutions.face_mesh

face_detection = mp_face_detection.FaceDetection(min_detection_confidence=0.5, model_selection=0)
hands = mp_hands.Hands(min_detection_confidence=0.5, min_tracking_confidence=0.5, max_num_hands=1)
face_mesh = mp_face_mesh.FaceMesh(
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
    max_num_faces=1,
    refine_landmarks=True
)

# Globals
camera = None
countdown_active = False
current_countdown = 0
latest_scary_score = 0
camera_lock = threading.Lock()

# Save to Downloads folder
SNAPSHOT_DIR = os.path.expanduser("~/Downloads")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# IP address of the other Raspberry Pi
OTHER_PI_URL = "http://192.168.50.48:3000/api/upload"

# Setup GPIO
GPIO.setwarnings(False)  # Ignore warnings
GPIO.setmode(GPIO.BOARD)  # Use physical pin numbering
GPIO.setup(7, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)  # Set pin 7 to be an input pin with pull-down

def calculate_scary_score(frame):
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb_frame.shape

    face_results = face_detection.process(rgb_frame)
    hand_results = hands.process(rgb_frame)
    face_mesh_results = face_mesh.process(rgb_frame)

    scary_score = 0
    face_center_y = None

    # Basic face detection
    if face_results.detections:
        bbox = face_results.detections[0].location_data.relative_bounding_box
        y = int(bbox.ymin * h)
        h_box = int(bbox.height * h)
        face_center_y = y + (h_box // 2)

    # Hand analysis
    if hand_results.multi_hand_landmarks and face_center_y is not None:
        for landmarks in hand_results.multi_hand_landmarks:
            hand_xs = []
            key_points = [4, 8, 20]
            for i, point in enumerate(landmarks.landmark):
                if i in key_points:
                    hand_x, hand_y = int(point.x * w), int(point.y * h)
                    hand_xs.append(hand_x)
                    if hand_y < face_center_y:
                        scary_score += 5  # More weight for hands above face

            # Finger spread bonus
            if len(hand_xs) >= 2:
                spread = max(hand_xs) - min(hand_xs)
                if spread >= 100:
                    scary_score += min(10, int((spread - 100) / 5))  # Bonus points up to +10

            break

    # Face mesh mouth/teeth analysis
    if face_mesh_results.multi_face_landmarks:
        landmarks = face_mesh_results.multi_face_landmarks[0]
        mouth_top = landmarks.landmark[13]
        mouth_bottom = landmarks.landmark[14]
        mouth_opening = abs(mouth_bottom.y - mouth_top.y) * h

        if mouth_opening > 20:
            scary_score += int(mouth_opening / 2)  # Bonus score for wider mouth

    # Scale score to 0-100
    scary_score = min(40, scary_score)
    return int((scary_score / 40) * 100)

def send_to_other_pi(image_path, score):
    try:
        upload_url = "http://192.168.50.64:3000/api/upload"
        # Use curl to POST the image file
        os.system(f'curl -X POST -F "image=@{image_path}" {upload_url}')
    except Exception as e:
        print(f"Failed to send image via curl: {e}")

def countdown_thread():
    global countdown_active, current_countdown, latest_scary_score, camera

    with camera_lock:
        try:
            # Open camera with index 0 only
            print(f"[{datetime.datetime.now()}] Opening camera at index 0")
            camera = cv2.VideoCapture(0)
            
            if not camera.isOpened():
                print(f"[{datetime.datetime.now()}] Failed to open camera at index 0")
                countdown_active = False
                current_countdown = 0
                latest_scary_score = -1
                return
            
            # Set camera properties
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
            # Countdown
            for i in range(3, 0, -1):
                current_countdown = i
                time.sleep(1)

            # Capture frame
            ret, frame = camera.read()
            
            if ret and frame is not None:
                latest_scary_score = calculate_scary_score(frame)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"scary_snapshot_{timestamp}.jpg"
                filepath = os.path.join(SNAPSHOT_DIR, filename)
                cv2.imwrite(filepath, frame)
                send_to_other_pi(filepath, latest_scary_score)
                print(f"[{datetime.datetime.now()}] Image captured and sent with score: {latest_scary_score}")
            else:
                print(f"[{datetime.datetime.now()}] Failed to capture image")
                latest_scary_score = -1

            # Release the camera
            camera.release()
            camera = None
            
        except Exception as e:
            print(f"[{datetime.datetime.now()}] Error in countdown thread: {str(e)}")
            if camera is not None:
                camera.release()
                camera = None
            latest_scary_score = -1

    countdown_active = False
    current_countdown = 0

# Button callback function
def button_callback(channel):
    global countdown_active
    print("Button was pushed! Starting scary score capture...")
    if not countdown_active:
        countdown_active = True
        threading.Thread(target=countdown_thread).start()

# Add event detection for button press
GPIO.add_event_detect(7, GPIO.RISING, callback=button_callback, bouncetime=300)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_countdown', methods=['POST'])
def start_countdown():
    global countdown_active
    if not countdown_active:
        countdown_active = True
        threading.Thread(target=countdown_thread).start()
        return jsonify({"status": "started"})
    else:
        return jsonify({"status": "already_running"})

@app.route('/get_status')
def get_status():
    return jsonify({
        "countdown_active": countdown_active,
        "current_countdown": current_countdown,
        "latest_scary_score": latest_scary_score
    })

@app.route('/get_score')
def get_score():
    return jsonify({"score": latest_scary_score})

if __name__ == '__main__':
    try:
        app.run(debug=False, host='0.0.0.0')  # Changed debug to False to avoid issues with GPIO
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()  # Clean up GPIO on program exit
