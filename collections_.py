# @author:  Gunnar Schaefer

import re
import json
import webapp2
import bson.json_util

import logging
log = logging.getLogger('nimsapi')

import nimsapiutil

# curator (later: multiple curators and authorizers)
# name
# permissions
# epochs point to collections

# /collections
# /collections/<cid>/sessions
# /collections/<cid>/epochs?session=<sid>
# /collections/<cid>/sessions/<sid>/epochs

class Collections(nimsapiutil.NIMSRequestHandler):

    """/collections """

    json_schema = {
        '$schema': 'http://json-schema.org/draft-04/schema#',
        'title': 'Collection List',
        'type': 'array',
        'items': {
            'title': 'Collection',
            'type': 'object',
            'properties': {
                '_id': {
                    'title': 'Database ID',
                },
                'site': {
                    'title': 'Site',
                    'type': 'string',
                },
                'group': {
                    'title': 'Group',
                    'type': 'string',
                },
                'name': {
                    'title': 'Name',
                    'type': 'string',
                },
                'permissions': {
                    'title': 'Permissions',
                    'type': 'object',
                },
            }
        }
    }

    def count(self):
        """Return the number of Collections."""
        self.response.write(json.dumps(self.app.db.collections.count()))

    def post(self):
        """Create a new Collection."""
        if not self.valid_parameters(): # FIXME: move to superclass init
            self.abort(400, 'invalid parameters')
        name = self.request.get('name') or 'innominate'
        epoch_ids = [bson.ObjectId(eid) for eid in self.request.get_all('epochs[]', [])]
        epochs = [self.app.db.epochs.find_one({'_id': eid}, ['session']) for eid in epoch_ids]
        if not all(epochs):
            self.abort(400, 'some Epoch IDs are invalid')
        if not all([self.user_access_epoch(epoch) for epoch in epochs]):
            self.abort(403, 'user does not have access to all Epochs')
        cid = self.app.db.collections.insert({'curator': self.userid, 'name': name, 'permissions': {self.userid: 'admin'}})
        for eid in epoch_ids:
            self.app.db.epochs.update({'_id': eid}, {'$push': {'collections': cid}})

    def get(self):
        """Return the list of Collections."""
        query = {'permissions.' + self.userid: {'$exists': 'true'}} if not self.user_is_superuser else None
        projection = ['curator', 'name', 'permissions.'+self.userid, 'notes']
        collections = list(self.app.db.collections.find(query, projection))
        self.response.write(json.dumps(collections, default=bson.json_util.default))

    def put(self):
        """Update many Collections."""
        self.response.write('collections put\n')


class Collection(nimsapiutil.NIMSRequestHandler):

    """/collections/<cid> """

    json_schema = {
        '$schema': 'http://json-schema.org/draft-04/schema#',
        'title': 'Collection',
        'type': 'object',
        'properties': {
            '_id': {
                'title': 'Database ID',
            },
            'site': {
                'title': 'Site',
                'type': 'string',
            },
            'group': {
                'title': 'Group',
                'type': 'string',
            },
            'name': {
                'title': 'Name',
                'type': 'string',
                'maxLength': 32,
            },
            'permissions': {
                'title': 'Permissions',
                'type': 'object',
                'minProperties': 1,
            },
            'files': {
                'title': 'Files',
                'type': 'array',
                'items': nimsapiutil.NIMSRequestHandler.file_schema,
                'uniqueItems': True,
            },
        },
        'required': ['_id', 'group', 'name'], #FIXME
    }

    def get(self, cid):
        """Return one Collection, conditionally with details."""
        collection = self.app.db.collections.find_one({'_id': bson.ObjectId(cid)})
        if not collection:
            self.abort(404)
        if not self.user_is_superuser:
            if self.userid not in collection['permissions']:
                self.abort(403)
            if collection['permissions'][self.userid] != 'admin': # mask other users' permissions
                collection['permissions'] = {self.userid: collection['permissions'][self.userid]}
        self.response.write(json.dumps(collection, default=bson.json_util.default))

    def put(self, cid):
        """Update an existing Collection."""
        cid = bson.ObjectId(cid)
        if not self.valid_parameters(): # FIXME: move to superclass init
            self.abort(400, 'invalid parameters')
        collection = self.app.db.collections.find_one({'_id': cid})
        if not collection:
            self.abort(404, 'Collection not found')
        if not self.user_is_superuser and collection['permissions'].get(self.userid) != 'admin':
            self.abort(403, 'user must be admin on Collection to modify')
        add_epoch_ids = [bson.ObjectId(eid) for eid in self.request.get_all('add_epochs[]', [])]
        add_epochs = [self.app.db.epochs.find_one({'_id': eid}, ['session']) for eid in add_epoch_ids]
        del_epoch_ids = [bson.ObjectId(eid) for eid in self.request.get_all('del_epochs[]', [])]
        del_epochs = [self.app.db.epochs.find_one({'_id': eid}, ['session']) for eid in del_epoch_ids]
        if not all(add_epochs + del_epochs):
            self.abort(400, 'some Epoch IDs are invalid')
        if not all([self.user_access_epoch(epoch) for epoch in add_epochs]):
            self.abort(403, 'user does not have access to all Epochs')
        for eid in add_epoch_ids:
            print 'adding', eid, 'to', cid
            self.app.db.epochs.update({'_id': eid}, {'$addToSet': {'collections': bson.ObjectId(cid)}})
        for eid in del_epoch_ids:
            self.app.db.epochs.update({'_id': eid}, {'$pull': {'collections': bson.ObjectId(cid)}})

    def delete(self, cid):
        """Delete a Collection."""
        cid = bson.ObjectId(cid)
        collection = self.app.db.collections.find_one({'_id': cid}, ['permissions'])
        if not collection:
            self.abort(404, 'Collection not found')
        if not self.user_is_superuser and collection['permissions'].get(self.userid) != 'admin':
            self.abort(403, 'user must be admin on Collection to delete')
        self.app.db.epochs.update({'collections': cid}, {'$pull': {'collections': cid}}, multi=True)
        self.app.db.collections.remove({'_id': cid})


class Sessions(nimsapiutil.NIMSRequestHandler):

    """/collections/<cid>/sessions """

    json_schema = {
        '$schema': 'http://json-schema.org/draft-04/schema#',
        'title': 'Session List',
        'type': 'array',
        'items': {
            'title': 'Session',
            'type': 'object',
            'properties': {
                '_id': {
                    'title': 'Database ID',
                },
                'name': {
                    'title': 'Session',
                    'type': 'string',
                },
                'subject': {
                    'title': 'Subject',
                    'type': 'string',
                },
                'site': {
                    'title': 'Site',
                    'type': 'string',
                },
            }
        }
    }

    def count(self):
        """Return the number of Sessions."""
        self.response.write(json.dumps(self.app.db.sessions.count()))

    def post(self):
        """Create a new Session"""
        self.response.write('sessions post\n')

    def get(self, cid):
        """Return the list of Session Epochs."""
        collection = self.app.db.collections.find_one({'_id': bson.ObjectId(cid)})
        if not collection:
            self.abort(404)
        if not self.user_is_superuser and self.userid not in collection['permissions']:
            self.abort(403)
        aggregated_epochs = self.app.db.epochs.aggregate([
                {'$match': {'collections': bson.ObjectId(cid)}},
                {'$group': {'_id': '$session'}},
                ])['result']
        query = {'_id': {'$in': [agg_epoch['_id'] for agg_epoch in aggregated_epochs]}}
        projection = ['name', 'subject', 'notes']
        sessions = list(self.app.db.sessions.find(query, projection))
        for sess in sessions:
            sess['site'] = self.app.config['site_id']
        self.response.write(json.dumps(sessions, default=bson.json_util.default))

    def put(self):
        """Update many Sessions."""
        self.response.write('sessions put\n')


class Epochs(nimsapiutil.NIMSRequestHandler):

    """/collections/<cid>/epochs """

    json_schema = {
        '$schema': 'http://json-schema.org/draft-04/schema#',
        'title': 'Epoch List',
        'type': 'array',
        'items': {
            'title': 'Epoch',
            'type': 'object',
            'properties': {
                '_id': {
                    'title': 'Database ID',
                },
                'name': {
                    'title': 'Epoch',
                    'type': 'string',
                },
                'description': {
                    'title': 'Description',
                    'type': 'string',
                },
                'datatype': {
                    'title': 'Datatype',
                    'type': 'string',
                },
            }
        }
    }

    def count(self):
        """Return the number of Epochs."""
        self.response.write(json.dumps(self.app.db.epochs.count()))

    def post(self):
        """Create a new Epoch."""
        self.response.write('epochs post\n')

    def get(self, cid):
        """Return the list of Session Epochs."""
        collection = self.app.db.collections.find_one({'_id': bson.ObjectId(cid)})
        if not collection:
            self.abort(404)
        if not self.user_is_superuser and self.userid not in collection['permissions']:
            self.abort(403)
        query = {'collections': bson.ObjectId(cid)}
        sid = self.request.get('session')
        if re.match(r'^[0-9a-f]{24}$', sid):
            query['session'] = bson.ObjectId(sid)
        elif sid != '':
            self.abort(400)
        projection = ['name', 'description', 'datatype', 'notes']
        epochs = list(self.app.db.epochs.find(query, projection))
        self.response.write(json.dumps(epochs, default=bson.json_util.default))

    def put(self):
        """Update many Epochs."""
        self.response.write('epochs put\n')
