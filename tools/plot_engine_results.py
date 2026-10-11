"""Render saved synthetic count matrices; requires the optional plots extra."""
import argparse
import json
from pathlib import Path


def render(path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    document=json.loads(Path(path).read_text(encoding='utf8'))
    paths=[]
    for name,metrics in document['engines'].items():
        matrix=metrics['confusion_matrix'];labels=list(matrix)
        values=[[matrix[a][b] for b in labels] for a in labels]
        fig,ax=plt.subplots(figsize=(10,8));im=ax.imshow(values,cmap='Blues')
        ax.set_xticks(range(len(labels)),labels,rotation=45);ax.set_yticks(range(len(labels)),labels)
        ax.set(xlabel='Predicted hypothesis',ylabel='Synthetic truth',title=f"{document['split']}: {name} ({metrics['accuracy']:.1%} accuracy)")
        limit=max(max(row) for row in values)/2
        for i,row in enumerate(values):
            for j,value in enumerate(row):ax.text(j,i,str(value),ha='center',va='center',color='white' if value>limit else 'black')
        fig.colorbar(im,ax=ax);fig.tight_layout()
        output=Path(path).parent/f'confusion-{name}.png';fig.savefig(output,dpi=150);plt.close(fig);paths.append(output)
    return paths


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('results',type=Path)
    for output in render(parser.parse_args().results):print(output)
