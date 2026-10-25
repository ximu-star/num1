# atguigu/import_process/nodes/node_pdf_to_md.py
import shutil
import time
import zipfile
from pathlib import Path

from atguigu.config.config import MineruConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodePDFToMD(NodeBase):
    """
    PDF 转 Markdown 节点：PDF结构化解析
    """

    name = "node_pdf_to_md"
    def check_path(self,state):
        #1、拿pdf文件的路径
        pdf_path=state.get('pdf_path','')
        if not pdf_path:
            logger.error("路径错误：pdf路径不存在")
            raise ValueError("路径错误：pdf路径不存在")
        #2、拿pdf路径中的pdf文件
        pdf_path_obj=Path(pdf_path)
        if not pdf_path_obj.exists():
            logger.error("路径错误：pdf路径内不存在pdf文件")
            raise FileNotFoundError("路径错误：pdf路径内不存在pdf文件")
        #3、获取输出目录即保存md文件的路径名
        local_dir=state.get('local_dir','')
        if not local_dir:
            logger.error("路径错误：local_dir输出路径不存在")
            raise ValueError("路径错误：local_dir输出路径不存在")
        #4、创建或获取输出目录下的输出文件夹
        local_dir_obj=Path(local_dir)
        if not local_dir_obj.exists():
            local_dir_obj.mkdir(parents=True, exist_ok=True)#自动创建多级父目录，目录已存在时不会抛异常
        return pdf_path,pdf_path_obj,local_dir,local_dir_obj

    def upload_pdf_to_mineru(self,pdf_path,pdf_path_obj):
        import requests

        token = MineruConfig.mineru_token
        url = MineruConfig.mineru_base_url
        header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        data = {
            "files": [
                {"name": f"{pdf_path_obj.name}", "data_id": "abcd"}
            ],
            "model_version": "vlm"
        }
        file_path = [f"{pdf_path}"]
        response = requests.post(url, headers=header, json=data)
        if response.status_code != 200:
            logger.error(f"上传pdf到mineru请求失败")
            raise Exception(f"上传pdf到mineru请求失败")
        logger.info(f"上传pdf到mineru请求成功")
        result = response.json()
        if result["code"] != 0:
            logger.error(f"上传pdf到mineru失败")
            raise Exception(f"上传pdf到mineru失败")
        logger.info(f"上传pdf到mineru成功")
        batch_id = result["data"]["batch_id"]
        urls = result["data"]["file_urls"]
        for i in range(0, len(urls)):
            with open(file_path[i], 'rb') as f:
                res_upload = requests.put(urls[i], data=f)
                if res_upload.status_code == 200:
                    logger.info(f"{urls[i]}上传成功")
                else:
                    logger.error(f"{urls[i]}上传失败")
        return batch_id

    def get_md_zip_url(self,batch_id):
        import requests

        token = MineruConfig.mineru_token
        batch_id = batch_id
        url = f"{MineruConfig.mineru_base_url_down}/{batch_id}"
        header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        total_time=600
        use_time=0
        while use_time<total_time:
            start_time=time.time()
            try:
                response = requests.get(url, headers=header)
                if response.status_code != 200:
                    logger.error(f"获取md文件请求失败")
                    raise Exception(f"获取md文件请求失败")
                result = response.json()
                if result["code"] != 0:
                    logger.error(f"获取md文件失败")
                    raise Exception(f"获取md文件失败")
                if result["data"]["extract_result"][0]["state"]!="done":
                    logger.error(f"pdf转md文件处理中")
                    raise Exception(f"pdf转md文件未完成")
                zip_url=result["data"]["extract_result"][0]["full_zip_url"]
                return zip_url
            except Exception as e:
                logger.error(f"获取md文件url异常，等待重试,{e}")
            finally:
                end_time=time.time()
                use_time+=end_time-start_time
        raise TimeoutError("获取超时")


    def download_zip(self,zip_url,local_dir_obj,pdf_path_obj):
        #1、发下载md_zip的请求
        import requests
        md_zip_res=requests.get(zip_url) #发请求
        if md_zip_res.status_code != 200:
            logger.error(f"下载md压缩包请求失败")
            raise Exception(f"下载md压缩包请求失败")
        #2、保存md_zip到磁盘
        md_zip_content=md_zip_res.content #拿到压缩包，但没保存到磁盘
        md_zip_path_obj=local_dir_obj/f"{pdf_path_obj.stem}.zip" #构造下载路径
        with open(md_zip_path_obj, "wb") as f:
            f.write(md_zip_content) #写入磁盘
        #3、解压md_zip到磁盘
        upzip_file_content=zipfile.ZipFile(md_zip_path_obj) #拿到解压内容
        upzip_file_path_obj=local_dir_obj/f"{pdf_path_obj.stem}" #构造解压路径
        if upzip_file_path_obj.exists():
            shutil.rmtree(upzip_file_path_obj)#强制删除以便重新创建
        upzip_file_path_obj.mkdir(parents=True, exist_ok=True)#自动递归创建所有缺失的上级文件夹+幂等特性
        upzip_file_content.extractall(upzip_file_path_obj) #把解压内容放到解压路径
        #4、对解压的md文件重命名
        origin_md_path_obj=upzip_file_path_obj/"full.md"
            # 原本的md文件叫full.md，我们要重命
        new_md_path_obj=origin_md_path_obj.with_name(f"{pdf_path_obj.stem}.md") #内存里面改名用with_name
        origin_md_path_obj.rename(new_md_path_obj) #磁盘上改名用rename
        with open(new_md_path_obj, "r", encoding="utf-8") as f:
            md_content = f.read()
        return md_content,new_md_path_obj

    def process(self, state: ImportGraphState):
        # 第一大步：校验pdf路径的存在
        pdf_path, pdf_path_obj, local_dir, local_dir_obj=self.check_path(state)
        # 第二大步：上传pdf到mineru获取batch_id
        batch_id=self.upload_pdf_to_mineru(pdf_path,pdf_path_obj)
        # 第三大步：等待mineru处理完成,我们需要轮询给mineru发请求，获取压缩包zip的url
        zip_url=self.get_md_zip_url(batch_id)
        # 第四大步：下载zip压缩文件，解压，重命名，把文件的内容读取保存state
        md_content,new_md_path_obj=self.download_zip(zip_url,local_dir_obj,pdf_path_obj)
        return {"md_content":md_content,"md_path":str(new_md_path_obj)}

if __name__ == "__main__":
    node=NodePDFToMD()
    init_state = {"pdf_path": r"E:\pdf_md\input\hak180产品安全手册.pdf",
                  "local_dir": r"E:\pdf_md\output"}
    result=node(init_state)
    logger.info(json_format(result))