import copy
import json
from pathlib import Path
import pytest
from scripts.summarize_component_progress import summarize


def fixture():
    b=json.loads((Path(__file__).resolve().parents[1]/'data/component-benchmark.json').read_bytes())
    preview={'sha256':'b'*64};archive={'url':'https://example.invalid/a','member':'files/a.svg'}
    f={'sha256':'a'*64,'size_bytes':1,'format':'svg','structure':'pure_vector','archive':archive,'preview':preview,
       'asset_ids':['a'],'family_ids':['a'],'public_scope':{'public_allowed':True}}
    row={'variant_id':'v','rank':1,'asset_id':'a','family_id':'a','selected_file':{k:f[k] for k in
         ['sha256','size_bytes','format','structure','archive','preview']},'preview':preview,'download':archive}
    raw={'queries':[{'query_id':q['query_id'],'exact_results':[copy.deepcopy(row)]} for q in b['queries']]}
    return b,raw,{'queries':[],'tasks':[]},{'files':[f]}


def test_nonempty_retrieval_without_observations_is_entirely_pending():
    b,raw,review,m=fixture();r=summarize(b,raw,review,m)
    assert r['nonempty_queries']==142 and r['task_language_unit_status']=={'pending':120}
    assert r['result_positions_reviewed']==0 and not r['acceptance_passed']


def test_missing_role_reviews_prove_category_failure_without_inventing_other_scores():
    b,raw,review,m=fixture()
    for q in b['queries'][:4]:  # Two distinct first-category tasks, both languages.
        review['queries'].append({'query_id':q['query_id'],'result_judgments':[{'variant_id':'v','rank':1,
            'original_sha256':'a'*64,'preview_sha256':'b'*64,'verdict':'irrelevant'}],
            'all_results_reviewed':True,'selected_variant_ids':[]})
    r=summarize(b,raw,review,m)
    assert r['known_threshold_failure'] and r['categories'][0]['bilingual_task_upper_bound']==3
    assert r['task_language_unit_status']=={'failed_required_role':4,'pending':116}
    assert r['formal_component_score'] is None


def test_partial_summary_cannot_open_release_even_if_all_component_reviews_pass():
    b,raw,review,m=fixture()
    review['queries']=[{'query_id':q['query_id'],'result_judgments':[{'variant_id':'v','rank':1,
        'original_sha256':'a'*64,'preview_sha256':'b'*64,'verdict':'relevant'}],
        'all_results_reviewed':True,'selected_variant_ids':['v']} for q in b['queries']]
    review['tasks']=[{'task_id':t['task_id'],'language':lang,'composition_use_pass':True,
        'selected_roles':{role['component_id']:['v'] for role in t['required_roles']}}
        for t in b['tasks'] for lang in ('zh','en')]
    r=summarize(b,raw,review,m)
    assert r['task_language_unit_status']=={'fully_reviewed_supported':120}
    assert r['acceptance_passed'] is False and r['website_gate']=='closed'


def test_false_completeness_claim_is_rejected():
    b,raw,review,m=fixture()
    review['queries']=[{'query_id':b['queries'][0]['query_id'],'result_judgments':[],
        'all_results_reviewed':True,'selected_variant_ids':[]}]
    with pytest.raises(ValueError,match='completeness'):
        summarize(b,raw,review,m)
