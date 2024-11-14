import os
import json
import requests
import pyrebase
from flask import Flask, redirect, request, render_template, send_from_directory, session, url_for
from google.cloud import storage
import google.generativeai as genai

keysFile = os.path.join(os.getcwd, "secret_keys.json")

with open(keysFile) as config:
   config = json.load(config)

app = Flask(__name__)
app.secret_key = config["app_secret"]

os.makedirs('files', exist_ok = True)
bucket_name = 'photo_app1'

firebaseConfig = {
  "apiKey": config["firebase_secret"],
  "authDomain": "composite-store-436104-d3.firebaseapp.com",
  "databaseURL": "https://composite-store-436104-d3-default-rtdb.firebaseio.com",
  "projectId": "composite-store-436104-d3",
  "storageBucket": "composite-store-436104-d3.appspot.com",
  "messagingSenderId": "917252237477",
  "appId": "1:917252237477:web:5e66b80eb562fb1088054c"
};

firebase = pyrebase.initialize_app(firebaseConfig)
auth = firebase.auth()

genai.configure(api_key=config["ai_secret"])

generation_config = {
  "temperature": 1,
  "top_p": 0.95,
  "top_k": 64,
  "max_output_tokens": 8192,
  "response_mime_type": "text/plain",
}

def upload_to_gemini(path, mime_type=None):
  file = genai.upload_file(path, mime_type=mime_type)
  print(f"Uploaded file '{file.display_name}' as: {file.uri}")
  return file

def gemini(imageFile):
  model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
  )

  files = upload_to_gemini(imageFile, mime_type="image/jpeg")

  chat_session = model.start_chat(
    history=[
      {
        "role": "user",
        "parts": [
          files,
          "Give a title and description for the image and give the response in json format",
        ],
      }
    ]
  )

  response = chat_session.send_message("INSERT_INPUT_HERE")
  return response.text

def blob_upload(bucket_name, source_file, destination_file, user_id):
  storage_client = storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(f"{user_id}/{destination_file}")
  blob.upload_from_file(source_file)

def blob_download(bucket_name, source_file, destination_file):
  os.makedirs(os.path.dirname(destination_file), exist_ok=True)
  storage_client = storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(source_file)
  blob.download_to_filename(destination_file)

def blob_list(bucket_name, user_id):
  storage_client = storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blobs = bucket.list_blobs(prefix=f"{user_id}/")
  
  image_names = []
  for blob in blobs:
    image_names.append(blob.name)

  return image_names

@app.route('/')
def index():
  user_id = session.get('user')
  if not user_id:
    return redirect('/login')

  user_directory = os.path.join('files', user_id)
  os.makedirs(user_directory, exist_ok=True)
  blob_names = blob_list(bucket_name, user_id)

  for names in blob_names:
      local_file_path = os.path.join(user_directory, names.split('/')[-1])
      if not os.path.exists(local_file_path):
          blob_download(bucket_name, names, local_file_path)

  local_files = os.listdir(user_directory)
  for user_file in local_files:
      user_path = os.path.join(user_directory, user_file)
      if os.path.isfile(user_path):
          if user_file not in [blob.split('/')[-1] for blob in blob_names]:
              os.remove(user_path)

  file_list = {}
  for file in blob_names:
    if file.lower().endswith(('.jpg', '.jpeg', '.png')):
      text_file = os.path.splitext(file)[0] + '.txt'
      description = None
      if os.path.exists(os.path.join(user_directory, text_file)):
        with open(os.path.join(user_directory, text_file), 'r') as textFile:
          description = textFile.read()
      if os.path.exists(os.path.join(user_directory, os.path.basename(file))):
        file_list[os.path.basename(file)] = description

  return render_template('index.html', filepaths=file_list, user_id=user_id)

@app.route('/upload', methods=['POST'])
def upload():
  if 'user' not in session:
    return redirect('/login')

  user_id = session['user']
  user_directory = os.path.join('files', user_id)

  os.makedirs(user_directory, exist_ok=True)
  print("hey",request.files)
  file = request.files['image_file']
  filename = file.filename
  path1 = os.path.join(user_directory, filename)
  file.save(path1)
  response = gemini(path1)

  try:
      response = response.replace('json', "").replace("```", "").strip()
      final_response = json.loads(response)
      title = final_response.get("title", "No Title Available")
      description = final_response.get("description", "No Title Available")
  except:
      print("No JSON response found")
      return "No response found"

  text_path = os.path.join(user_directory, os.path.splitext(filename)[0] + '.txt')
  with open(text_path, 'w') as text_file:
      text_file.write(f"{title}\n{description}")

  with open(text_path, 'rb') as text_file1:
      blob_upload(bucket_name, text_file1, os.path.basename(text_path), user_id)

  file.seek(0)
  blob_upload(bucket_name, file, os.path.basename(path1), user_id)

  return redirect('/')

@app.route('/files')
def list_files():
    files = os.listdir("./files")
    jpegs = []
    for file in files:
        if file.lower().endswith(".jpeg") or file.lower().endswith(".jpg"):
            jpegs.append(file)
    return jpegs

@app.route('/files/<user_id>/<filename>')
def get_users_files(filename, user_id):
    return send_from_directory(os.path.join('files', user_id), filename)

def extract_file_content(content):
  lines = content.split('\n')
  if lines:
    title = lines[0].strip()
  else:
    title = "No Title"
  if len(lines)>1:
    description = '\n'.join(lines[1:]).strip() 
  else:
    description = "No Description"

  return [title, description]

@app.route('/view/<user_id>/<filename>')
def view_users_files(user_id, filename):
  text_file = os.path.splitext(filename)[0]+'.txt'
  title = "No Title"
  description = "No Description"

  path = os.path.join('./files', user_id, text_file)
  if os.path.exists(path):
    with open(path, 'r') as text_path2:
      content = text_path2.read()
      content_list = extract_file_content(content)

  return render_template('image.html', user_id=user_id, filename=filename, title=content_list[0], description=content_list[1])

@app.route('/signup', methods=['GET', 'POST'])
def signup():
  if request.method == 'POST':
    email = request.form["email"]
    password = request.form["password"]

    try:
      user = auth.create_user_with_email_and_password(email, password)
      print(user)
      session['user'] = user['localId']
      return redirect('/')
    except Exception:
      return f"Error: {str(Exception)}"
    
  return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
  if request.method == 'POST':
    email = request.form["email"]
    password = request.form["password"]
    print(email, password)
    try:
      user = auth.sign_in_with_email_and_password(email, password)
      session["user"] = user['localId']
      return redirect('/')
    except:
      return "Invalid login details"
  
  return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
  session.pop('user', None)
  return redirect('/login')

if __name__ == "__main__":
    app.run(port=8080, debug=True)