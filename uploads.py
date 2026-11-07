"""Bounded ZIP import. Never extract paths supplied by an archive."""
import io,json,re,stat,zipfile
from pathlib import PurePosixPath
from engine import strict_document
MAX_UPLOAD=64*1024*1024
MAX_EXPANDED=256*1024*1024
MAX_FILES=50000

def dataset_zip(raw,target):
    if len(raw)>MAX_UPLOAD:raise ValueError('Архив больше 64 МБ')
    target.mkdir(parents=True,exist_ok=True);seen=set();sizes=0;counts={'BEFORE_MASTER':0,'AFTER_BETA':0};ids=set();invalid=0;examples=[]
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
            if len(tail)!=2 or tail[0] not in (*counts,'BEFORE','AFTER') or not re.fullmatch(r'[A-Za-z0-9_-]+\.json',tail[1]):raise ValueError('Ожидается RESULTS/{BEFORE_MASTER,AFTER_BETA}/ID.json')
            tail=({'BEFORE':'BEFORE_MASTER','AFTER':'AFTER_BETA'}.get(tail[0],tail[0]),tail[1])
            key=tuple(tail)
            if key in seen:raise ValueError('Повтор файла в ZIP')
            seen.add(key);content=z.read(item)
            try:strict_document(content)
            except (ValueError,TypeError,UnicodeError,RecursionError) as ex:
                invalid+=1
                if len(examples)<20:examples.append({'source':tail[0],'app_id':tail[1][:-5],'error':(type(ex).__name__+': '+str(ex))[:300]})
            dest=target/'RESULTS'/tail[0]/tail[1];dest.parent.mkdir(exist_ok=True,parents=True);dest.write_bytes(content)
            counts[tail[0]]+=1;ids.add(tail[1])
    if not sum(counts.values()):raise ValueError('В архиве нет JSON-файлов в RESULTS/BEFORE_MASTER или RESULTS/AFTER_BETA')
    for source in counts:(target/'RESULTS'/source).mkdir(parents=True,exist_ok=True)
    return dict(files=sum(counts.values()),apps=len(ids),slices=counts,uncompressed_bytes=sizes,invalid_responses=invalid,validation_examples=examples)
