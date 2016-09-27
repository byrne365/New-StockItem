from flask import Flask, render_template, request, redirect, jsonify, url_for, flash
from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker
from database_setup import Base, MusicShop, StockItem, User
from flask import session as login_session
import random
import string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
from flask import make_response
import requests

app = Flask(__name__)

CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']
APPLICATION_NAME = "Music Shop Application"


# Connect to Database and create database session
engine = create_engine('sqlite:///musicshop.db')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


# Create anti-forgery state token
@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    # return "The current session state is %s" % login_session['state']
    return render_template('login.html', STATE=state)


@app.route('/fbconnect', methods=['POST'])
def fbconnect():
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    access_token = request.data
    print "access token received %s " % access_token

    app_id = json.loads(open('fb_client_secrets.json', 'r').read())[
        'web']['app_id']
    app_secret = json.loads(
        open('fb_client_secrets.json', 'r').read())['web']['app_secret']
    url = 'https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id=%s&client_secret=%s&fb_exchange_token=%s' % (
        app_id, app_secret, access_token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]

    # Use token to get user info from API
    userinfo_url = "https://graph.facebook.com/v2.4/me"
    # strip expire tag from access token
    token = result.split("&")[0]


    url = 'https://graph.facebook.com/v2.4/me?%s&fields=name,id,email' % token
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]
    # print "url sent for API access:%s"% url
    # print "API JSON result: %s" % result
    data = json.loads(result)
    login_session['provider'] = 'facebook'
    login_session['username'] = data["name"]
    login_session['email'] = data["email"]
    login_session['facebook_id'] = data["id"]

    # The token must be stored in the login_session in order to properly logout, let's strip out the information before the equals sign in our token
    stored_token = token.split("=")[1]
    login_session['access_token'] = stored_token

    # Get user picture
    url = 'https://graph.facebook.com/v2.4/me/picture?%s&redirect=0&height=200&width=200' % token
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]
    data = json.loads(result)

    login_session['picture'] = data["data"]["url"]

    # see if user exists
    user_id = getUserID(login_session['email'])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']

    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '

    flash("Now logged in as %s" % login_session['username'])
    return output


@app.route('/fbdisconnect')
def fbdisconnect():
    facebook_id = login_session['facebook_id']
    # The access token must me included to successfully logout
    access_token = login_session['access_token']
    url = 'https://graph.facebook.com/%s/permissions?access_token=%s' % (facebook_id,access_token)
    h = httplib2.Http()
    result = h.request(url, 'DELETE')[1]
    return "you have been logged out"


@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # obtain authorization code
    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

        # Check that the access token is valid
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
            % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # Abort if there was an error in the access token info.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify access token is valid for this app
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app."), 401)
        print "Token's client id does not match app."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps('Current user is already connected'),
                                200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store access token in session for later use
    login_session['access_token'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']
    # Add provider to login session
    login_session['provider'] = 'google'

    # See if user exists, if not create a new one
    user_id = getUserID(data["email"])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h2>Welcome, '
    output += login_session['username']
    output += '!</h2>'
    output += '<img src = "'
    output += login_session['picture']
    output += ' "style = "width: 200px; height: 200px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;">'
    flash("you are now logged in as %s" % login_session['username'])
    print "done!"
    return output

# User helper functions

def createUser(login_session):
    newUser = User(name=login_session['username'], email=login_session[
                   'email'], picture=login_session['picture'])
    session.add(newUser)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.id


def getUserInfo(user_id):
    user = session.query(User).filter_by(id=user_id).one()
    return user


def getUserID(email):
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None

# Disconnect - revoke current user's token and reset login_session

@app.route('/gdisconnect')
def gdisconnect():
    # Only disconnect a connected user
    credentials = login_session.get('credentials')
    if credentials is None:
        response = make_response(
            json.dumps('Current user not connected'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    access_token = credentials.access_token
    url = 'https://www.googleapis.com/o/oauth2/revoke?token=%s' % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]
    if result['status'] != '200':
        #If token was invalid
        response = make_response(
            json.dumps('Failed to revoke token for given user', 400))
        response.headers['Content-Type'] = 'application/json'
        return response


# JSON APIs to view Music Shop Information
@app.route('/music_shop/<int:music_shop_id>/stock/JSON')
def music_shopStockJSON(music_shop_id):
    music_shop = session.query(MusicShop).filter_by(id=music_shop_id).one()
    stock = session.query(StockItem).filter_by(
        music_shop_id=music_shop_id).all()
    return jsonify(StockItems=[s.serialize for s in stock])


@app.route('/music_shop/<int:music_shop_id>/stock/<int:stock_id>/JSON')
def stockItemJSON(music_shop_id, stock_id):
    Stock_Item = session.query(StockItem).filter_by(id=stock_id).one()
    return jsonify(Stock_Item=Stock_Item.serialize)


@app.route('/music_shop/JSON')
def music_shopsJSON():
    music_shops = session.query(MusicShop).all()
    return jsonify(music_shops=[m.serialize for m in music_shops])


# Show all music shops
@app.route('/')
@app.route('/music_shop/')
def showMusicShops():
    music_shops = session.query(MusicShop).order_by(asc(MusicShop.name))
    if 'username' not in login_session:
        return render_template('publicmusic_shops.html', music_shops=music_shops)
    else:
        return render_template('music_shops.html', music_shops=music_shops)

# Create a new music shop


@app.route('/music_shop/new/', methods=['GET', 'POST'])
def newMusicShop():
    if 'username' not in login_session:
        return redirect('/login')
    if request.method == 'POST':
        newMusicShop = MusicShop(
            name=request.form['name'], user_id=login_session['user_id'])
        session.add(newMusicShop)
        flash('New Music Shop %s Successfully Created' % newMusicShop.name)
        session.commit()
        return redirect(url_for('showMusicShops'))
    else:
        return render_template('newMusicShop.html')

# Edit a Music Shop


@app.route('/music_shop/<int:music_shop_id>/edit/', methods=['GET', 'POST'])
def editMusicShop(music_shop_id):
    editedMusicShop = session.query(
        MusicShop).filter_by(id=music_shop_id).one()
    if 'username' not in login_session:
        return redirect('/login')
    if editedMusicShop.user_id != login_session['user_id']:
        return "<script>function myFunction() {alert('You are not authorized to edit this music shop. Please create your own music shop in order to edit.');}</script><body onload='myFunction()''>"
    if request.method == 'POST':
        if request.form['name']:
            editedMusicShop.name = request.form['name']
            flash('Music Shop Successfully Edited %s' % editedMusicShop.name)
            return redirect(url_for('showMusicShops'))
    else:
        return render_template('editMusicShops.html', music_shop=editedMusicShop)


# Delete a Music Shop
@app.route('/music_shop/<int:music_shop_id>/delete/', methods=['GET', 'POST'])
def deleteMusicShop(music_shop_id):
    music_shopToDelete = session.query(
        MusicShop).filter_by(id=music_shop_id).one()
    if 'username' not in login_session:
        return redirect('/login')
    if music_shopToDelete.user_id != login_session['user_id']:
        return "<script>function myFunction() {alert('You are not authorized to delete this music shop. Please create your own music shop in order to delete.');}</script><body onload='myFunction()''>"
    if request.method == 'POST':
        session.delete(music_shopToDelete)
        flash('%s Successfully Deleted' % music_shopToDelete.name)
        session.commit()
        return redirect(url_for('showMusicShops', music_shop_id=music_shop_id))
    else:
        return render_template('deleteMusicShop.html', music_shop=music_shopToDelete)

# Show a stock items
@app.route('/music_shop/<int:music_shop_id>/')
@app.route('/music_shop/<int:music_shop_id>/stock/')
def showStock(music_shop_id):
    music_shop = session.query(MusicShop).filter_by(id=music_shop_id).one()
    creator = getUserInfo(music_shop.user_id)
    items = session.query(StockItem).filter_by(
        music_shop_id=music_shop_id).all()
    if 'username' not in login_session or creator.id != login_session['user_id']:
        return render_template('publicstock.html', items=items, music_shop=music_shop, creator=creator)
    else:
        return render_template('stock.html', items=items, music_shop=music_shop, creator=creator)


# Create a new stock item
@app.route('/music_shop/<int:music_shop_id>/stock/new/', methods=['GET','POST'])
def newStockItem(music_shop_id):
    if 'username' not in login_session:
        return redirect('/login')
        print 'ok'
    music_shop = session.query(MusicShop).filter_by(id=music_shop_id).one()
    newItem = session.query(StockItem).filter_by(music_shop_id = music_shop_id)
    print 'ok2'
    if login_session['user_id'] != music_shop.user_id:
        return "<script>function myFunction() {alert('You are not authorized to add stock items to this music shop. Please create your own music shop in order to add items.');}</script><body onload='myFunction()''>"
        if request.method == 'POST':
            newItem = StockItem(name=request.form['name'], description=request.form['description'], price=request.form[
                               'price'], instrument=request.form['instrument'], music_shop_id=music_shop_id, user_id=music_shop.user_id)
            session.add(newItem)
            session.commit()
            print 'done'
            flash('New Stock %s Item Successfully Created' % (newItem.name))
        return redirect(url_for('showStock', music_shop_id=music_shop_id))
    else:
        return render_template('newstockitem.html', music_shop_id=music_shop_id)

# Edit a stock item


@app.route('/music_shop/<int:music_shop_id>/stock/<int:stock_id>/edit', methods=['GET', 'POST'])
def editStockItem(music_shop_id, stock_id):
    if 'username' not in login_session:
        return redirect('/login')
    editedItem = session.query(StockItem).filter_by(id=stock_id).one()
    music_shop = session.query(MusicShop).filter_by(id=music_shop_id).one()
    if login_session['user_id'] != music_shop.user_id:
        return "<script>function myFunction() {alert('You are not authorized to edit stock items to this music shop. Please create your own music shop in order to edit stock items.');}</script><body onload='myFunction()''>"
    if request.method == 'POST':
        if request.form['name']:
            editedItem.name = request.form['name']
        if request.form['description']:
            editedItem.description = request.form['description']
        if request.form['price']:
            editedItem.price = request.form['price']
        if request.form['instrument']:
            editedItem.course = request.form['instrument']
        session.add(editedItem)
        session.commit()
        flash('Stock Item Successfully Edited')
        return redirect(url_for('showStock', music_shop_id=music_shop_id))
    else:
        return render_template('editstockitem.html', music_shop_id=music_shop_id, stock_id=stock_id, item=editedItem)


# Delete a stock item
@app.route('/music_shop/<int:music_shop_id>/stock/<int:stock_id>/delete', methods=['GET', 'POST'])
def deleteStockItem(music_shop_id, stock_id):
    if 'username' not in login_session:
        return redirect('/login')
    music_shop = session.query(MusicShop).filter_by(id=music_shop_id).one()
    itemToDelete = session.query(StockItem).filter_by(id=stock_id).one()
    if login_session['user_id'] != music_shop.user_id:
        return "<script>function myFunction() {alert('You are not authorized to delete stock items from this music shop. Please create your own music shop in order to delete stock items.');}</script><body onload='myFunction()''>"
    if request.method == 'POST':
        session.delete(itemToDelete)
        session.commit()
        flash('Stock Item Successfully Deleted')
        return redirect(url_for('showStock', music_shop_id=music_shop_id))
    else:
        return render_template('deleteStockItem.html', item=itemToDelete)


# Disconnect based on provider
@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
        if login_session['provider'] == 'facebook':
            fbdisconnect()
            del login_session['facebook_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have been successfully logged out")
        return redirect(url_for('showMusicShops'))
    else:
        flash("You were not logged in.")
        return redirect(url_for('showMusicShops'))


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=8000)