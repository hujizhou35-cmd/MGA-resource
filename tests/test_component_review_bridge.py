import copy
import pytest
from scripts.bridge_component_reviews import reusable_result, bridge


def row():
    return {'asset_id':'one','family_id':'one','variant_id':'v1','title':'Rat',
            'selected_source':{'source':'fixture','source_id':'one'},
            'selected_file':{'sha256':'a'*64,'scientific':{'species':['rat']}},
            'preview':{'sha256':'b'*64}, 'download':{'member':'one.svg'},
            'selected_science':{'species':['rat'],'primary_object':{
                'terms':['rat','rats'], 'basis':'direct_source_title_compound_label','depiction_kind':'animal'}}}


def test_alias_only_reuse_does_not_relabel_source_science():
    old=row(); new=copy.deepcopy(old)
    new['selected_science']['primary_object']['terms']=['rat','rats','大鼠']
    safe, detail=reusable_result(old,new)
    assert safe and detail['derived_alias_terms_changed']
    assert old['selected_science']['primary_object']['terms']==['rat','rats']


@pytest.mark.parametrize('field',['source','file','preview','science','depiction'])
def test_observation_cannot_cross_source_file_or_scientific_changes(field):
    old=row(); new=copy.deepcopy(old)
    if field=='source': new['selected_source']['source_id']='two'
    if field=='file': new['selected_file']['sha256']='c'*64
    if field=='preview': new['preview']['sha256']='d'*64
    if field=='science': new['selected_science']['species']=['mouse']
    if field=='depiction': new['selected_science']['primary_object']['depiction_kind']='equipment'
    assert reusable_result(old,new)[0] is False


def test_reuse_rejects_changed_task_purpose_before_copying_verdicts():
    bindings={'component_benchmark_sha256':'a','legacy_benchmark_sha256':'b','catalog_sha256':'c'}
    query={'query_id':'one','request':{'query':'rat'},'task_id':'task','component_id':'object',
           'language':'en','review_requirements':{'required_species':'rat'},'exact_results':[]}
    old={'bindings':bindings,'queries':[query]}; new=copy.deepcopy(old)
    new['queries'][0]['review_requirements']['required_species']='mouse'
    review={'bindings':{'raw_results_sha256':'old'},'queries':[{'query_id':'one','result_judgments':[]}],'tasks':[]}
    with pytest.raises(ValueError,match='Changed request/use'):
        bridge(old,new,[(review,{'document':{},'old_raw_sha256':'old'})],{'catalog_sha256':'c','files':[]})
