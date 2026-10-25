# import pymongo
# myclient = pymongo.MongoClient("mongodb://localhost:27017/")
# mydb = myclient["runoobdb"]
# mycol = mydb["sites"]
import time

from pymongo import MongoClient

from atguigu.config.config import MongoConfig

mongo_client=None
def get_mongo_client():
    global mongo_client
    if not mongo_client:
        mongo_client=MongoClient(MongoConfig.mongo_url)
    return mongo_client

collection=None
db=None
def get_mongo_collection():
    global collection
    global db
    mongo_client=get_mongo_client()
    if db is None:
        db = mongo_client[MongoConfig.mongo_db_name]
    if collection is None:
        collection = db.get_collection("chat_history")
        collection.create_index([("id",1),("ts",-1),("session_id",1)])
    return collection

# 创建对历史记录增删改查的方法
# 1、获取最近的多少条历史记录 limit来限定  方法目的是后期再意图识别的时候需要获取历史记录来识别
def get_recent_history_list(session_id,limit=10):
    collection=get_mongo_collection()
    result=collection.find({"session_id":session_id}).sort("ts",-1).limit(limit)
    return list(result)

def add_or_update_history(session_id,role,text,rewritten_query=None,item_names=None,image_urls=None,ts=None,_id=None):
# 全量更新和增量更新
# 为什么人们在封装数据库增和改的时候全部合二为一写一个方法或者函数，就是因为他们传递参数的时候唯一不同就是id
# 如果是修改，那么id一定存在
# 如果是新增，那么id一定不存在
    collection=get_mongo_collection()
    if _id:#修改
        data={
            "_id":_id,
            "session_id":session_id,
            "role":role,
            "text":text,
            "rewritten_query":rewritten_query,
            "item_names":item_names,
            "image_urls": image_urls,
            "ts":ts or time.time()
        }
        collection.update_one({"_id":_id},{"$set":data})
        return _id
    else:#新增
        data={
            "session_id":session_id,
            "role":role,
            "text":text,
            "rewritten_query":rewritten_query,
            "item_names":item_names,
            "image_urls": image_urls,
            "ts":ts or time.time()
        }
        result=collection.insert_one(data)
        return result.inserted_id

def clear_history(session_id):
    collection=get_mongo_collection()
    collection.delete_many({"session_id":session_id})
    # return collection.delete_many({"session_id":session_id}).deleted_count

def update_item_names_and_query(ids,item_names=None,rewritten_query=None):
    collection=get_mongo_collection()
    data={"item_names":item_names,"rewritten_query":rewritten_query}
    collection.update_many({"_ids":{"$in":ids}},{"$set":data})