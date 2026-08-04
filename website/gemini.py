from google import genai
import os
API_KEY = os.getenv("GENAI_API_KEY")

client = genai.GeminiClient(api_key=API_KEY)




#---- upload ---
#uploaded_file = client.files.upload(file="")

#response = client.models.generate_content(
#    model="gemini-2.0-flash",
#    prompt=f"Write a detailed summary of the following document: {uploaded_file.id}",
#)
#print(response.text)


#---- Interactive Chat Example ----
#chat = client.chats.create(model="gemini-2.0-flash")

#while True:
#    message = input("> ")
#    if message.lower() in ["exit", "quit"]:
#        break

#    res = chat.send_message(message)
#    print(res.text)

#---- example ----
#response = client.models.generate_content_stream(
#    model="gemini-2.0-flash",
#    prompt="Write a short poem about the beauty of nature.",
#)

#for stream in response:
#    print(stream.text, end="")