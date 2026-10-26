import json

from bson import ObjectId


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)

def json_format(data):
    return json.dumps(data, ensure_ascii=False, indent=4,cls=NumpyEncoder)
    # ensure_ascii=False：中文不会被转义成\uXXXX
    # indent=4：换行缩进4空格，排版美观易读