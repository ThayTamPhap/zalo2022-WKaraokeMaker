import argparse


import torch

from kmaker.dataloader import *
from kmaker.model import *
from time import time

def convert_result_to_competion_format(pred_word_segments, json_path, word_idx_to_milisec_ratio):
    """
        pred_word_segments: predictions list 
                [['Những', 0.26025, 0.4204375],
                ['niềm', 0.5405625, 0.8209375],
                ['đau', 1.12125, 1.2814375],...]
        json_path: competion format output placeholder
    """

    pred_i = 0
    target = mmcv.load(json_path)
    for i, line in enumerate(target):
        for j, word in enumerate(line['l']):
            
            pred_word = pred_word_segments[pred_i]
            s = int(pred_word.start*word_idx_to_milisec_ratio)
            e = int(pred_word.end*word_idx_to_milisec_ratio)
            
            if j == 0:
                target[i]['s'] = s
            elif j == len(line['l'])-1:
                target[i]['e'] = e
                
            target[i]['l'][j]['s'] = s
            target[i]['l'][j]['e'] = e
            
            pred_i += 1
    return target


def preproc(path):
    item = ItemAudioLabel(path, spliter='|', is_training=False) 
    rt =  dict(inputs=item.mel)
    rt.update(item.get_words_meta())
    rt['w2v_tokens'] = item.w2v_tokens
    # assert max(rt['w2v_tokens'])<110, rt['w2v_tokens']
    rt['idx'] = None
    rt['transcript'] = item.transcript
    audio = item.audio
    json_path = item.path

    batch = collate_fn([rt], False)
    with torch.inference_mode():
        for k, v in batch.items():
            batch[k] = v.cuda() if hasattr(v, 'cuda') else v

    return item, batch


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ckpt')
    parser.add_argument('data')
    parser.add_argument('exp_name')
    parser.add_argument('--sot', action='store_true')
    parser.add_argument('--max_samples', '-m', default=None, type=int)
    args = parser.parse_args()
    st = torch.load(args.ckpt)
    if 'state_dict' in st:
        st = st['state_dict']
        
    collate_fn = collate_fn_v2 if args.sot else collate_fn_v1
    json_paths = glob(args.data+'/labels/*.json')
    # ds = AudioDataset([ItemAudioLabel(json_path)  for json_path in json_paths])
    model = get_whisper('base')
    modified_model = modify_whisper(model, args.sot)

    # model = get_whisper('base')
    # modified_model = modify_whisper(model)
    new_st = {k[6:]:v for k, v in st.items()}
    modified_model.load_state_dict(new_st)
    eval_model = modified_model.cuda().requires_grad_(False).eval()

        

    all_predicted_time = []
    all_result = []
    if args.max_samples is not None:
        json_paths = json_paths[0::len(json_paths)//args.max_samples]

    for i, path in tqdm(enumerate(json_paths)):
        t1 = time()
        item, batch = preproc(path)
        with torch.inference_mode():

            outputs = eval_model.forward_both(
                        batch['inputs'],
                        labels=batch['labels'],
                        ctc_labels=batch['w2v_labels'],
                    )
            bboxes = outputs['bbox_pred'][batch['dec_pos']]

        
        
        xyxy = box_cxcywh_to_xyxy(bboxes)[:,[0,2]]
        words = [_[0] for _ in item.words]
        
        results = []
        xyxy = xyxy.clip(0, 1)
        for (x1,x2), word in zip((xyxy*30).tolist(), words):
            results.append((word, x1, x2))
        results = [Segment(*result, 1) for result in results]
        results = convert_result_to_competion_format(results, path, 1000)
        t2 = time()
        
        all_result.append(results)
        predicted_time = int(t2*1000 - t1*1000)
        
        all_predicted_time.append((item.name, predicted_time))


    names = [get_name(path) for path in json_paths]
    if 'public' in args.data:
        test_set = 'public'
    elif 'private' in args.data:
        test_set = 'private'
    else:
        test_set = 'training'
        

    for results, name in zip(all_result, names):
        mmcv.dump(results, f'outputs/{test_set}_{args.exp_name}/submission/{name}.json')

    os.system(f'cd outputs/{test_set}_{args.exp_name} && zip -r {test_set}_{args.exp_name}.zip submission')
    print('Output: {}'.format(osp.abspath(f'outputs/{test_set}_{args.exp_name}.zip')))

