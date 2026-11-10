"""Run inside procedure-service with python - < this file."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import profile_binding as binding


class ProfileBindingTests(unittest.TestCase):
    def test_custom_form_ignores_modified_identity(self):
        content = {'sections': [{'blocks': [{'instance_id': 'applicant', 'fields': [
            {'key': 'name', 'autofill': 'profile.full_name'},
            {'key': 'id', 'autofill': 'profile.cccd_number'},
            {'key': 'reason'},
        ]}]}]}
        payload = SimpleNamespace(values={'applicant.name': 'Đã sửa trái phép', 'applicant.id': 'fake', 'applicant.reason': 'Giữ nguyên'})
        with patch.object(binding, 'load_profile_values', return_value={'profile.full_name': 'Tên đăng ký', 'profile.cccd_number': '001234567890'}):
            binding.bind_custom_profile(content, payload, 'user-id')
        self.assertEqual(payload.values['applicant.name'], 'Tên đăng ký')
        self.assertEqual(payload.values['applicant.id'], '001234567890')
        self.assertEqual(payload.values['applicant.reason'], 'Giữ nguyên')

    def test_other_people_rows_are_not_bound_to_account(self):
        content = {'sections': [{'blocks': [{'block_key': 'repeat_group', 'instance_id': 'people', 'fields': [{'key': 'name', 'autofill': 'profile.full_name'}]}]}]}
        with patch.object(binding, 'load_profile_values') as fetch:
            binding.bind_custom_profile(content, SimpleNamespace(values={}), 'user')
            fetch.assert_not_called()

    def test_standard_schema_only_binds_applicant(self):
        schema = SimpleNamespace(fields=[SimpleNamespace(name=name) for name in ('applicant_name', 'applicant_id_type', 'buyer_name')])
        with patch.object(binding, 'load_profile_values', return_value={'profile.full_name': 'Tên đăng ký'}):
            data = binding.bind_application_profile(schema, {'applicant_name': 'fake', 'buyer_name': 'Người khác'}, 'user')
        self.assertEqual(data['applicant_name'], 'Tên đăng ký')
        self.assertEqual(data['applicant_id_type'], 'CCCD')
        self.assertEqual(data['buyer_name'], 'Người khác')

    def test_birth_year_and_address(self):
        result = binding.profile_values({'date_of_birth': '1990-05-20', 'permanent_address': {'detail': 'Số 1', 'ward': 'Phường A', 'province': 'Tỉnh B'}, 'current_same_as_permanent': True})
        self.assertEqual(result['profile.birth_year'], '1990')
        self.assertEqual(result['profile.permanent_address'], result['profile.current_address'])


if __name__ == '__main__':
    unittest.main()
