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
from PIL import Image
import requests

import subprocess

app = Flask(__name__, static_folder='.')
CORS(app)  # Enable CORS for all routes

# Configuration
UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'qrcodes'
BMP_FOLDER = 'qrcodes_bmp'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'}
EXPIRATION_TIME = 30 * 60  # 30 minutes in seconds
CLEANUP_INTERVAL = 5 * 60  # 5 minutes in seconds
PORT = 3000
IMAGE_METADATA_FILE = 'image_metadata.json'
DISPLAY_COMMAND = ["sudo","./epd"]
# Create necessary directories if they don't exist
for folder in [UPLOAD_FOLDER, QR_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

app.config['BMP_FOLDER'] = BMP_FOLDER
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['QR_FOLDER'] = QR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload size

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_server_url():
    """Get the server's URL for network access"""
    # Always use Render URL for QR codes
    render_url = "https://qrcodegeneration2.onrender.com"
    print(f"[{datetime.datetime.now()}] Using Render URL for QR codes: {render_url}")
    return render_url

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
        print(f"[{datetime.datetime.now()}] Generating QR code for URL: {url}")
        
        # Make sure the URL is properly formatted
        if not url.startswith('http://') and not url.startswith('https://'):
            url = 'http://' + url
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # Higher error correction
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        qr_file_path = os.path.join(app.config['QR_FOLDER'], f"{image_id}_qr.png")
        img.save(qr_file_path)
        
        bmp = qr.make_image(fill="black", back_color="white").convert("1")
        bmp_width, bmp_height = bmp.size
        target_width, target_height = 800, 480

        padded_bmp = Image.new("1", (target_width, target_height), 1)
        x_offset = (target_width - bmp_width) // 2
        y_offset = (target_height - bmp_height) // 2
        padded_bmp.paste(bmp, (x_offset, y_offset))
        bmp_file_path = os.path.join(app.config['BMP_FOLDER'], f"{image_id}_qr.bmp")
        padded_bmp.save(bmp_file_path, format="BMP")


        print(f"[{datetime.datetime.now()}] QR code saved to: {qr_file_path}")
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

@app.route('/einkdisplay')
def einkdisplay():
    subprocess.run(DISPLAY_COMMAND)
    return redirect(url_for("index"))

@app.route('/api/url', methods=['POST'])
def receive_qr_url():
    """Handle URL submission for QR code generation"""
    print(f"[{datetime.datetime.now()}] URL QR Code endpoint was called")
    
    try:
        # Try to get JSON data first
        data = request.get_json()
        if data and 'url' in data:
            url = data['url']
        else:
            # Fall back to form data
            url = request.form.get('url')
            
        if not url:
            print(f"[{datetime.datetime.now()}] No URL provided in request")
            return jsonify({'error': 'No URL provided'}), 400

        print(f"[{datetime.datetime.now()}] Received URL to encode: {url}")
        
        # Check if this is a local image URL
        if ("192.168" in url or "localhost" in url or "127.0.0.1" in url) and ("/uploads/" in url):
            # This is a local image URL that needs to be uploaded to Render
            try:
                # Extract image path from URL
                image_path = url.split("/uploads/", 1)[1]
                local_path = os.path.join(UPLOAD_FOLDER, image_path)
                
                if os.path.exists(local_path):
                    # Upload the image to your Render site
                    upload_cmd = f'curl -X POST -F "image=@{local_path}" https://qrcodegeneration2.onrender.com/api/upload'
                    print(f"[{datetime.datetime.now()}] Uploading image to Render: {upload_cmd}")
                    
                    # Execute the upload command
                    result = os.system(upload_cmd)
                    
                    if result == 0:
                        # If upload succeeds, replace the URL with the Render URL
                        url = f"https://qrcodegeneration2.onrender.com/view/{image_path.split('.')[0]}"
                        print(f"[{datetime.datetime.now()}] Transformed URL for Render: {url}")
                    else:
                        print(f"[{datetime.datetime.now()}] Failed to upload image to Render")
                else:
                    print(f"[{datetime.datetime.now()}] Local image not found: {local_path}")
            except Exception as e:
                print(f"[{datetime.datetime.now()}] Error processing image upload: {str(e)}")
        elif "192.168" in url or "localhost" in url or "127.0.0.1" in url:
            # For other local URLs, just transform to Render domain
            path = url.split('/', 3)[-1] if '/' in url.split('//', 1)[-1] else ""
            url = f"https://qrcodegeneration2.onrender.com/{path}"
            print(f"[{datetime.datetime.now()}] Transformed URL for Render: {url}")

        # Generate QR code and BMP with the Render URL
        image_id = "remote"  # Overwrites same file each time for remote URLs
        qr_url = generate_qr_code(url, image_id)
        
        if not qr_url:
            return jsonify({'error': 'Failed to generate QR code'}), 500

        # Save image ID for your epd display to read
        with open("current_qrcode.txt", "w") as f:
            f.write(f"{image_id}_qr\n")

        # Display on e-ink
        time.sleep(1)  # Optional buffer before display
        subprocess.run(DISPLAY_COMMAND)

        return jsonify({
            'success': True,
            'status': 'QR code displayed',
            'url': url,
            'qrUrl': qr_url
        }), 200
        
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Error processing URL request: {str(e)}")
        return jsonify({'error': f'Error processing request: {str(e)}'}), 500

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
    # Use absolute URL with Render domain for the image
    render_url = "https://qrcodegeneration2.onrender.com"
    image_url = f"{render_url}/uploads/{image_data['filename']}"
    
    print(f"[{datetime.datetime.now()}] Serving view for image: {image_data['original_filename']}")
    
    # Calculate time left
    now = time.time()
    age_in_seconds = now - image_data['upload_time']
    seconds_remaining = max(0, EXPIRATION_TIME - age_in_seconds)
    minutes_remaining = int(seconds_remaining / 60) + 1
    
    # Generate HTML with absolute URLs for all resources
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Image - {image_data['original_filename']}</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
        <style>
            /* CSS styles remain the same */
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
                    <a href="{render_url}/download/{image_id}" class="button download">
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

import requests

@app.route('/api/upload', methods=['POST'])
def api_upload_file():
    """Handle API file uploads with JSON response"""
    print(f"[{datetime.datetime.now()}] API Upload endpoint was called")
    
    if 'image' not in request.files:
        print(f"[{datetime.datetime.now()}] No file part in the request")
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        print(f"[{datetime.datetime.now()}] No file selected")
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        try:
            image_id = str(uuid.uuid4())
            original_filename = secure_filename(file.filename)
            extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
            unique_filename = f"{image_id}.{extension}" if extension else image_id
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            
            # Skip uploading to Render if already on Render
            if not IS_RENDER:
                render_url = "https://qrcodegeneration2.onrender.com/api/upload"
                print(f"[{datetime.datetime.now()}] Uploading to Render: {render_url}")
                
                # Use requests to upload the file to Render
                with open(file_path, 'rb') as f:
                    files = {'image': (unique_filename, f)}
                    response = requests.post(render_url, files=files)
                
                if response.status_code != 200:
                    print(f"[{datetime.datetime.now()}] Warning: Failed to upload to Render. Status: {response.status_code}, Response: {response.text}")
                    # Optionally, you can handle the failure (e.g., return an error)
            
            upload_time = time.time()
            view_url = f"https://qrcodegeneration2.onrender.com/view/{image_id}"
            qr_url = generate_qr_code(view_url, image_id)
            
            if not qr_url:
                print(f"[{datetime.datetime.now()}] Failed to generate QR code for image ID: {image_id}")
                return jsonify({'error': 'Failed to generate QR code'}), 500
            
            with open("current_qrcode.txt", "w") as f:
                f.write(f"{image_id}_qr\n")
            
            metadata = load_metadata()
            metadata[image_id] = {
                'filename': unique_filename,
                'original_filename': original_filename,
                'upload_time': upload_time,
                'size': os.path.getsize(file_path)
            }
            save_metadata(metadata)
            
            print(f"[{datetime.datetime.now()}] Successfully saved file: {unique_filename} (ID: {image_id})")
            
            time.sleep(1)
            subprocess.run(DISPLAY_COMMAND)
            
            return jsonify({
                'success': True,
                'id': image_id,
                'name': original_filename,
                'url': f"/uploads/{unique_filename}",
                'qrUrl': qr_url,
                'viewUrl': view_url,
                'downloadUrl': f"https://qrcodegeneration2.onrender.com/download/{image_id}",
                'timeLeft': 30
            }), 200
            
        except Exception as e:
            print(f"[{datetime.datetime.now()}] Error processing upload: {e}")
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
    result = api_upload_file()
    
    # Since api_upload_file returns a tuple (response, status_code) when called directly,
    # we need to handle it properly
    if isinstance(result, tuple):
        response, status_code = result
    else:
        response, status_code = result, 200  # Fallback in case of unexpected return
    
    # If the upload was successful, redirect to the homepage
    if status_code == 200:
        return redirect('/')
    else:
        # Convert JSON error to string
        error_message = response.get_json().get('error', 'Unknown error') if isinstance(response, app.response_class) else str(response)
        return f"Error: {error_message}", status_code

if __name__ == '__main__':
    print(f"[{datetime.datetime.now()}] Server running at {get_server_url()}")
    print(f"[{datetime.datetime.now()}] Access this server from any device on your network using the URL above")
    app.run(host='0.0.0.0', port=PORT, debug=True)
