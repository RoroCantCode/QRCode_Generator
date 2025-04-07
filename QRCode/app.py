from flask import Flask, request, jsonify, send_from_directory, render_template, abort, redirect, url_for
from flask_cors import CORS
import os
import time
import uuid
import datetime
import socket
import threading
import json
import qrcode
from io import BytesIO
from werkzeug.utils import secure_filename
import requests
import json

app = Flask(__name__, static_folder='.')
CORS(app)  # Enable CORS for all routes

# Configuration
UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'qrcodes'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'}
EXPIRATION_TIME = 30 * 60  # 30 minutes in seconds
CLEANUP_INTERVAL = 5 * 60  # 5 minutes in seconds
PORT = 3000
IMAGE_METADATA_FILE = 'image_metadata.json'

# Create necessary directories if they don't exist
for folder in [UPLOAD_FOLDER, QR_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

QR_DISPLAY_PI = "http://192.168.50.48:3000/api/url"  # Replace with actual address

def send_image_url_to_display_pi(image_url):
    try:
        # Method 1: Using curl (more reliable in some network configurations)
        cmd = f'curl -X POST -H "Content-Type: application/json" -d \'{{"url":"{image_url}"}}\' http://192.168.50.48:3000/api/url'
        print(f"[{datetime.datetime.now()}] Executing curl command: {cmd}")
        result = os.system(cmd)
        
        if result == 0:
            print(f"[{datetime.datetime.now()}] Successfully sent image URL to display Pi: {image_url}")
        else:
            print(f"[{datetime.datetime.now()}] Failed to send URL. Command returned: {result}")
            
            # Fallback method: try with form data instead
            print(f"[{datetime.datetime.now()}] Trying fallback method with form data")
            fallback_cmd = f'curl -X POST -F "url={image_url}" http://192.168.50.48:3000/api/url'
            fallback_result = os.system(fallback_cmd)
            
            if fallback_result == 0:
                print(f"[{datetime.datetime.now()}] Fallback method successful")
            else:
                print(f"[{datetime.datetime.now()}] Fallback method also failed with code: {fallback_result}")
                
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Error sending image URL: {e}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['QR_FOLDER'] = QR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload size

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_server_url():
    """Get the server's IP address for network access"""
    # First, check if a custom server URL is provided via environment variable
    import os
    custom_url = os.environ.get('SERVER_URL')
    if custom_url:
        print(f"[{datetime.datetime.now()}] Using custom server URL from environment: {custom_url}")
        return custom_url
    
    # Try to get all network interfaces that could be used
    possible_ips = []
    
    # Method 1: Using socket connection trick
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))  # Google's DNS server
        ip = s.getsockname()[0]
        s.close()
        if ip and ip != '127.0.0.1':
            possible_ips.append(ip)
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Method 1 failed: {e}")
    
    # Method 2: Get all network interfaces using built-in socket module
    try:
        import subprocess
        import re
        
        # For Unix/Linux/Mac
        if os.name != "nt":
            try:
                # Try using ifconfig
                output = subprocess.check_output(['ifconfig']).decode('utf-8')
                ip_pattern = re.compile(r'inet\s+(\d+\.\d+\.\d+\.\d+)')
                for ip in ip_pattern.findall(output):
                    if ip != '127.0.0.1' and ip not in possible_ips:
                        possible_ips.append(ip)
            except (subprocess.SubprocessError, FileNotFoundError):
                try:
                    # Try using ip addr
                    output = subprocess.check_output(['ip', 'addr']).decode('utf-8')
                    ip_pattern = re.compile(r'inet\s+(\d+\.\d+\.\d+\.\d+)')
                    for ip in ip_pattern.findall(output):
                        if ip != '127.0.0.1' and ip not in possible_ips:
                            possible_ips.append(ip)
                except (subprocess.SubprocessError, FileNotFoundError):
                    print(f"[{datetime.datetime.now()}] Could not get network interfaces using system commands")
        # For Windows
        else:
            try:
                output = subprocess.check_output(['ipconfig']).decode('utf-8')
                ip_pattern = re.compile(r'IPv4.+?:\s+(\d+\.\d+\.\d+\.\d+)')
                for ip in ip_pattern.findall(output):
                    if ip != '127.0.0.1' and ip not in possible_ips:
                        possible_ips.append(ip)
            except subprocess.SubprocessError:
                print(f"[{datetime.datetime.now()}] Could not get network interfaces using ipconfig")
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Method 2 failed: {e}")
    
    # Method 3: Hostname resolution
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
        if ip and ip != '127.0.0.1' and ip not in possible_ips:
            possible_ips.append(ip)
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Hostname resolution failed: {e}")
    
    # Filter and prioritize IPs
    # Prefer IPs in common private network ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
    filtered_ips = []
    for ip in possible_ips:
        if ip.startswith('192.168.') or ip.startswith('10.') or any(ip.startswith(f'172.{i}.') for i in range(16, 32)):
            filtered_ips.append(ip)
    
    # Use the first private IP if available, otherwise use any available IP
    server_ip = filtered_ips[0] if filtered_ips else (possible_ips[0] if possible_ips else 'localhost')
    
    server_url = f"http://{server_ip}:{PORT}"
    print(f"[{datetime.datetime.now()}] Detected server URL: {server_url}")
    print(f"[{datetime.datetime.now()}] All possible IPs: {possible_ips}")
    
    # Return the server URL
    return server_url

def load_metadata():
    """Load the image metadata from the JSON file"""
    if os.path.exists(IMAGE_METADATA_FILE):
        try:
            with open(IMAGE_METADATA_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading metadata: {e}")
    return {}

def save_metadata(metadata):
    """Save the image metadata to the JSON file"""
    try:
        with open(IMAGE_METADATA_FILE, 'w') as f:
            json.dump(metadata, f, indent=2)
    except Exception as e:
        print(f"Error saving metadata: {e}")

def generate_qr_code(url, image_id):
    """Generate a QR code for a given URL and save it"""
    try:
        import qrcode_terminal  # Add this if not already at the top

        print(f"[{datetime.datetime.now()}] Generating QR code for URL: {url}")
        
        # Make sure the URL is properly formatted
        if not url.startswith('http://') and not url.startswith('https://'):
            url = 'http://' + url
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,  # Higher error correction
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        qr_file_path = os.path.join(app.config['QR_FOLDER'], f"{image_id}_qr.png")
        img.save(qr_file_path)

        print(f"[{datetime.datetime.now()}] QR code saved to: {qr_file_path}")
        print(f"[{datetime.datetime.now()}] QR code link: {url}")
        send_image_url_to_display_pi(url)
        qrcode_terminal.draw(url)

        return f"/qrcodes/{image_id}_qr.png"
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Error generating QR code: {e}")
        return None

def cleanup_old_files():
    """Remove files older than EXPIRATION_TIME"""
    while True:
        print(f"[{datetime.datetime.now()}] Running cleanup check...")
        now = time.time()
        metadata = load_metadata()
        deleted_ids = []
        
        try:
            for image_id, image_data in list(metadata.items()):
                file_path = os.path.join(UPLOAD_FOLDER, image_data['filename'])
                qr_path = os.path.join(QR_FOLDER, f"{image_id}_qr.png")
                
                if os.path.isfile(file_path):
                    # Check if file has expired
                    if now - image_data['upload_time'] > EXPIRATION_TIME:
                        # Delete both the image and its QR code
                        os.remove(file_path)
                        if os.path.exists(qr_path):
                            os.remove(qr_path)
                        deleted_ids.append(image_id)
                        print(f"[{datetime.datetime.now()}] Deleted old file: {image_data['filename']}")
                else:
                    # File doesn't exist, remove from metadata
                    if os.path.exists(qr_path):
                        os.remove(qr_path)
                    deleted_ids.append(image_id)
            
            # Remove deleted files from metadata
            for image_id in deleted_ids:
                metadata.pop(image_id, None)
            
            save_metadata(metadata)
        except Exception as e:
            print(f"[{datetime.datetime.now()}] Error during cleanup: {e}")
            
        # Wait for the next cleanup interval
        time.sleep(CLEANUP_INTERVAL)

# Start the cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    """Serve static files"""
    if os.path.exists(path):
        return send_from_directory('.', path)
    else:
        abort(404)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve the uploaded images"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/qrcodes/<filename>')
def qrcode_file(filename):
    """Serve the QR code images"""
    return send_from_directory(app.config['QR_FOLDER'], filename)

@app.route('/download/<image_id>')
def download_image(image_id):
    """Download an image with proper headers to force download"""
    metadata = load_metadata()
    
    if image_id not in metadata:
        return "Image not found or has expired", 404
        
    image_data = metadata[image_id]
    file_path = os.path.join(UPLOAD_FOLDER, image_data['filename'])
    
    if not os.path.exists(file_path):
        return "File not found", 404
    
    # Set headers to force download with correct filename
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        image_data['filename'],
        as_attachment=True,
        download_name=image_data['original_filename']
    )

@app.route('/view/<image_id>')
def view_image(image_id):
    """View a single image page, accessible by scanning QR code"""
    print(f"[{datetime.datetime.now()}] View image request for ID: {image_id}")
    metadata = load_metadata()
    
    if image_id not in metadata:
        print(f"[{datetime.datetime.now()}] Image ID not found: {image_id}")
        return "Image not found or has expired", 404
        
    image_data = metadata[image_id]
    image_url = f"/uploads/{image_data['filename']}"
    
    # Use relative URL for the image to avoid cross-origin issues
    server_url = get_server_url()
    print(f"[{datetime.datetime.now()}] Serving view for image: {image_data['original_filename']}")
    
    # Calculate time left
    now = time.time()
    age_in_seconds = now - image_data['upload_time']
    seconds_remaining = max(0, EXPIRATION_TIME - age_in_seconds)
    minutes_remaining = int(seconds_remaining / 60) + 1
    
    # Generate HTML for single image view - without back button
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Image - {image_data['original_filename']}</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
        <style>
            :root {{
                --primary-color: #3498db;
                --primary-dark: #2980b9;
                --secondary-color: #2ecc71;
                --secondary-dark: #27ae60;
                --background-color: #f5f7fa;
                --card-color: #ffffff;
                --text-color: #333333;
                --text-light: #888888;
                --shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                --border-radius: 8px;
                --transition: all 0.3s ease;
            }}

            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: var(--background-color);
                color: var(--text-color);
                line-height: 1.6;
                padding: 20px;
                max-width: 100%;
                margin: 0 auto;
                display: flex;
                flex-direction: column;
                min-height: 100vh;
            }}

            header {{
                text-align: center;
                margin-bottom: 20px;
                padding-bottom: 10px;
                border-bottom: 1px solid #e1e1e1;
            }}

            header h1 {{
                color: var(--primary-color);
                font-size: 1.8rem;
                margin-bottom: 10px;
            }}

            .image-card {{
                background-color: var(--card-color);
                border-radius: var(--border-radius);
                box-shadow: var(--shadow);
                padding: 20px;
                margin-bottom: 20px;
                flex: 1;
                display: flex;
                flex-direction: column;
            }}

            .image-container {{
                flex: 1;
                display: flex;
                align-items: center;
                justify-content: center;
                margin-bottom: 20px;
                overflow: hidden;
            }}

            .image-container img {{
                max-width: 100%;
                max-height: 70vh;
                object-fit: contain;
            }}

            .image-info {{
                display: flex;
                flex-direction: column;
                gap: 10px;
            }}

            .image-name {{
                font-weight: 600;
                font-size: 1.2rem;
                color: var(--text-color);
            }}

            .image-expiry {{
                font-size: 0.9rem;
                color: var(--text-light);
                display: flex;
                align-items: center;
                gap: 5px;
            }}

            .image-expiry i {{
                color: var(--primary-color);
            }}

            .action-buttons {{
                display: flex;
                justify-content: center;
                margin-top: 15px;
            }}

            .button {{
                display: flex;
                align-items: center;
                justify-content: center;
                background-color: var(--secondary-color);
                color: white;
                text-decoration: none;
                padding: 10px 20px;
                border-radius: var(--border-radius);
                transition: var(--transition);
                font-weight: 500;
                gap: 5px;
                cursor: pointer;
                width: 100%;
                max-width: 250px;
            }}

            .button.download {{
                background-color: var(--secondary-color);
            }}

            .button.download:hover {{
                background-color: var(--secondary-dark);
            }}

            footer {{
                text-align: center;
                margin-top: auto;
                padding-top: 20px;
                font-size: 0.8rem;
                color: var(--text-light);
            }}
        </style>
    </head>
    <body>
        <header>
            <h1>Image Viewer</h1>
        </header>

        <div class="image-card">
            <div class="image-container">
                <img src="{image_url}" alt="{image_data['original_filename']}">
            </div>
            <div class="image-info">
                <div class="image-name">{image_data['original_filename']}</div>
                <div class="image-expiry">
                    <i class="fas fa-clock"></i> Expires in {minutes_remaining} minutes
                </div>
                <div class="action-buttons">
                    <a href="/download/{image_id}" class="button download">
                        <i class="fas fa-download"></i> Download
                    </a>
                </div>
            </div>
        </div>

        <footer>
            This image will be automatically deleted after 30 minutes from upload.
        </footer>
    </body>
    </html>
    """
    
    return html

@app.route('/api/images', methods=['GET'])
def get_images():
    """API endpoint to list all images and their expiration times"""
    print(f"[{datetime.datetime.now()}] API endpoint /api/images was called")
    
    try:
        metadata = load_metadata()
        images = []
        now = time.time()
        server_url = get_server_url()
        
        for image_id, image_data in metadata.items():
            # Calculate time left before expiration
            age_in_seconds = now - image_data['upload_time']
            seconds_remaining = max(0, EXPIRATION_TIME - age_in_seconds)
            minutes_remaining = int(seconds_remaining / 60) + 1
            
            # Check if file still exists
            if os.path.exists(os.path.join(UPLOAD_FOLDER, image_data['filename'])):
                # Get QR code URL or generate if not exists
                qr_path = f"/qrcodes/{image_id}_qr.png"
                if not os.path.exists(os.path.join(QR_FOLDER, f"{image_id}_qr.png")):
                    view_url = f"{server_url}/view/{image_id}"
                    send_image_url_to_display_pi(view_url)
                    qr_path = generate_qr_code(view_url, image_id)
                
                images.append({
                    'id': image_id,
                    'name': image_data['original_filename'],
                    'url': f"/uploads/{image_data['filename']}",
                    'qrUrl': qr_path,
                    'viewUrl': f"/view/{image_id}",
                    'downloadUrl': f"/download/{image_id}",  # Added downloadUrl
                    'timeLeft': minutes_remaining
                })
        
        print(f"[{datetime.datetime.now()}] Sending response with {len(images)} images")
        
        return jsonify({
            'serverUrl': server_url,
            'images': images
        })
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Error in /api/images: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def api_upload_file():
    """Handle API file uploads with JSON response"""
    print(f"[{datetime.datetime.now()}] API Upload endpoint was called")
    
    # Check if a file was provided
    if 'image' not in request.files:
        print(f"[{datetime.datetime.now()}] No file part in the request")
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['image']
    
    # Check if the file has a name
    if file.filename == '':
        print(f"[{datetime.datetime.now()}] No file selected")
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        try:
            # Generate a unique ID for the image
            image_id = str(uuid.uuid4())
            
            # Secure the filename and add a unique prefix
            original_filename = secure_filename(file.filename)
            extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
            unique_filename = f"{image_id}.{extension}" if extension else image_id
            
            # Save the file
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            
            # Record upload time for expiration
            upload_time = time.time()
            
            # Generate QR code for direct access
            server_url = get_server_url()
            view_url = f"{server_url}/view/{image_id}"
            qr_url = generate_qr_code(view_url, image_id)
            
            # Record metadata
            metadata = load_metadata()
            metadata[image_id] = {
                'filename': unique_filename,
                'original_filename': original_filename,
                'upload_time': upload_time,
                'size': os.path.getsize(file_path)
            }
            save_metadata(metadata)
            
            print(f"[{datetime.datetime.now()}] Successfully saved file: {unique_filename} (ID: {image_id})")
            
            # Return success with image info
            return jsonify({
                'success': True,
                'id': image_id,
                'name': original_filename,
                'url': f"/uploads/{unique_filename}",
                'qrUrl': qr_url,
                'viewUrl': f"/view/{image_id}",
                'downloadUrl': f"/download/{image_id}",  # Added downloadUrl
                'timeLeft': 30  # Initial expiration time in minutes
            }), 200
            
        except Exception as e:
            print(f"[{datetime.datetime.now()}] Error saving file: {e}")
            return jsonify({'error': str(e)}), 500
    else:
        print(f"[{datetime.datetime.now()}] File type not allowed")
        return jsonify({'error': 'File type not allowed'}), 400

# Traditional form submission route (for backward compatibility)
@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle traditional form uploads with redirect"""
    print(f"[{datetime.datetime.now()}] Traditional upload endpoint was called")
    
    # Process the upload using the API function
    result, status_code = api_upload_file()
    
    # If it's JSON response, check for success
    if status_code == 200:
        return redirect('/')
    else:
        # Convert JSON error to string
        error_message = result.get_json().get('error', 'Unknown error')
        return f"Error: {error_message}", status_code

if __name__ == '__main__':
    print(f"[{datetime.datetime.now()}] Server running at {get_server_url()}")
    print(f"[{datetime.datetime.now()}] Access this server from any device on your network using the URL above")
    app.run(host='0.0.0.0', port=PORT, debug=True)
