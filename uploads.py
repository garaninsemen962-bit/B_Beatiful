"""Bounded ZIP import. Never extract paths supplied by an archive."""
import io,json,re,stat,zipfile
from pathlib import PurePosixPath
MAX_UPLOAD=64*1024*1024
MAX_EXPANDED=256*1024*1024
MAX_FILES=50000

def dataset_zip(raw,target):
    if len(raw)>MAX_UPLOAD:raise ValueError('Архив больше 64 МБ')
    target.mkdir(parents=True,exist_ok=True);seen=set();sizes=0;counts={'BEFORE_MASTER':0,'AFTER_BETA':0};ids=set()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files=z.infolist()
        if len(files)>MAX_FILES:raise ValueError('Слишком много файлов')
        for item in files:
            path=PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename:raise ValueError('Небезопасный путь в ZIP')
            mode=item.external_attr>>16
            if stat.S_ISLNK(mode):raise ValueError('Ссылки в ZIP не поддерживаются')
            sizes+=item.file_size
            if sizes>MAX_EXPANDED or item.file_size>4*1024*1024:raise ValueError('Распакованные данные слишком велики')
            if item.is_dir():continue
            parts=path.parts
            if 'RESULTS' not in parts:continue
            i=parts.index('RESULTS');tail=parts[i+1:]
            if len(tail)!=2 or tail[0] not in counts or not re.fullmatch(r'[A-Za-z0-9_-]+\.json',tail[1]):raise ValueError('Ожидается RESULTS/{BEFORE_MASTER,AFTER_BETA}/ID.json')
            key=tuple(tail)
            if key in seen:raise ValueError('Повтор файла в ZIP')
            seen.add(key);content=z.read(item);doc=json.loads(content.decode('utf-8-sig'))
            if not isinstance(doc,dict):raise ValueError('Ответ должен быть JSON-объектом')
            dest=target/'RESULTS'/tail[0]/tail[1];dest.parent.mkdir(exist_ok=True,parents=True);dest.write_bytes(content)
            counts[tail[0]]+=1;ids.add(tail[1])
    if not all(counts.values()):raise ValueError('Архив должен содержать оба непустых среза')
    return dict(files=sum(counts.values()),apps=len(ids),slices=counts,uncompressed_bytes=sizes)
