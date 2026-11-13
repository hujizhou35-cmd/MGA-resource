"""Protect source/artwork identities when applying bounded observations."""
import copy
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('reviewed_update', Path(__file__).resolve().parents[1] / 'scripts/update_reviewed_metadata.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def example():
    return [{'id': 'a', 'title': 'tube', 'files': [
        {'sha256': 'a'*64, 'preview': {'sha256': 'b'*64}, 'archive': {'member':'files/a.svg'},
         'public_scope': {'public_allowed': True}, 'source_records': [{'license':'CC0-1.0'}]}]}]


def test_bounded_edit_preserves_original_and_metadata():
    old = example()
    new = copy.deepcopy(old)
    new[0]['files'][0]['scientific'] = {'object': ['blood sample tube']}
    proof = module.verify_bounded_changes(old,new,{('a'*64,'b'*64)})
    assert proof['changed_unique_pairs'] == 1 and proof['all_other_catalog_fields_equal']


@pytest.mark.parametrize('field,value', [('sha256','c'*64),('preview',{'sha256':'c'*64}),
    ('archive',{'member':'files/different.svg'}), ('public_scope',{'public_allowed':False}),
    ('source_records',[{'license':'CC-BY-4.0'}])])
def test_reject_unexpected_non_scientific_changes(field, value):
    old = example(); new = copy.deepcopy(old)
    new[0]['files'][0][field] = value
    with pytest.raises(ValueError, match='data changed'):
        module.verify_bounded_changes(old,new,{('a'*64,'b'*64)})


def test_reject_unobserved_pair_and_unchanged_review_target():
    old = example(); new = copy.deepcopy(old)
    new[0]['files'][0]['scientific'] = {'object':'tube'}
    with pytest.raises(ValueError, match='unreviewed'):
        module.verify_bounded_changes(old,new,{('c'*64,'d'*64)})
    with pytest.raises(ValueError, match='not updated'):
        module.verify_bounded_changes(old,old,{('a'*64,'b'*64)})
