import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://bdtesteo123:bd1234567@cluster0.1mhseuj.mongodb.net/")
MONGO_DB = os.getenv("MONGO_DB", "NexusAccountDB")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", 6379)
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")