import json
import time
import uuid
from queue import Queue

import uvicorn
from fastapi import FastAPI, Path, BackgroundTasks
from fastapi.params import Body
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from atguigu.query_process.main_graph import QueryMainGraphRunner
from atguigu.tool.mongo_client_tool import get_recent_history_list, clear_history
from atguigu.tool.task_utils import update_task_status, TASK_STATUS_PROCESSING, get_task_info, TASK_STATUS_COMPLETED, \
    TASK_STATUS_FAILED, create_queue, put_data, get_data

app = FastAPI(
    title="掌柜智库检索模块对应的接口服务",
    description="检索模块各个api接口服务",
    version="0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 第一步：健康检查接口
@app.get("/health")
async def health():
    return {"status": "healthy"}

#第二步：写历史记录查询接口
@app.get("/history/{session_id}")
async def get_history(session_id: str=Path(..., description="会话id")):
#获取历史记录返回
    history_list= get_recent_history_list(session_id)
    history_list=[{"_id":str(item.get("_id")),
                   "role": item.get("role",""),
                   "text": item.get("text",""),
                   "rewritten_query": item.get("rewritten_query",""),
                   "item_names": item.get("item_names",""),
                   "ts": item.get("ts",""),
                   "session_id": item.get("session_id","")}
                  for item in history_list]
    history_list=sorted(history_list, key=lambda x: x.get("ts", 0))
    return {"items": history_list}#前端已经完成的情况下，返回数据的时候要看前端页面写的是什么，公司里一般是后端先干

#第三步：删除历史记录接口
@app.delete("/history/{session_id}")
async def delete_history(session_id: str=Path(..., description="会话id")):
    clear_history(session_id)
    return {"msg": "删除success"}

#第四步：查询接口
class QueryParams(BaseModel):
    session_id: str = Field(..., description="会话id")
    query: str = Field(..., description="查询内容")

queue_dict={}
def run_main_graph2(task_id, original_query, session_id):
    #造队列
    # if not queue_dict.get(task_id):
    #     queue_dict[task_id] = Queue()
    # q=queue_dict.get(task_id)
    create_queue(task_id)

    try:
        init_state={
            "task_id": task_id,
            "session_id": session_id,
            "original_query": original_query,
            # "q":q
        }
        #更新总状态，放到队列（默认有序），sse后期可以从队列中取出更新的数据状态推送给前端
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        # q.put({"event":"progress","data":get_task_info(task_id)})
        put_data(task_id, event="progress",data=get_task_info(task_id))

        QueryMainGraphRunner.create_and_run(init_state)

        update_task_status(task_id, TASK_STATUS_COMPLETED)
        # q.put({"event": "progress", "data": get_task_info(task_id)})
        put_data(task_id, event="progress", data=get_task_info(task_id))
    except Exception as e:
        update_task_status(task_id, TASK_STATUS_FAILED)
        # q.put({"event": "error", "data": get_task_info(task_id)})
        put_data(task_id, event="error", data=get_task_info(task_id))
        raise e


@app.post("/query")
async def query(background_tasks: BackgroundTasks, query_params: QueryParams=Body(..., description="查询参数")):
    #创建task_id，后期需要追踪
    task_id = str(uuid.uuid4())
    original_query = query_params.query
    session_id = query_params.session_id
    #第五步：调后台接口任务去执行graph
    background_tasks.add_task(run_main_graph2, task_id, original_query, session_id)
    return {"task_id": task_id,
            "original_query": original_query,
            "session_id": session_id}


def generate_stream(task_id):
    # while not queue_dict.get(task_id):
    #     time.sleep(1)
    # q=queue_dict.get(task_id)
    while True:
        item = get_data(task_id)
        time.sleep(0.05)
        yield f"event:{item.get('event')}\n"
        yield f"data:{json.dumps(item.get('data'),ensure_ascii=False)}\n\n"


@app.get("/stream/{task_id}")
async def stream(task_id: str=Path(..., description="任务id")):
    return StreamingResponse(generate_stream(task_id), media_type="text/event-stream")





if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8001)