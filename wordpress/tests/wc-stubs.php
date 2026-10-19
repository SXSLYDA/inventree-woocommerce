<?php
/**
 * Just enough of WooCommerce to exercise the gateway inside real WordPress
 * (user meta, sanitising, hooks) without a full WooCommerce install.
 */
class WC_Payment_Gateway {
	public $id; public $enabled = 'yes'; public $settings = array(); public $form_fields = array();
	public $title; public $description; public $has_fields; public $method_title; public $method_description;
	public $supports = array();
	public function init_form_fields() {}
	public function init_settings() {
		$saved = get_option( 'woocommerce_' . $this->id . '_settings', array() );
		foreach ( $this->form_fields as $k => $f ) {
			$this->settings[ $k ] = isset( $saved[ $k ] ) ? $saved[ $k ] : ( isset( $f['default'] ) ? $f['default'] : '' );
		}
		$this->enabled = $this->settings['enabled'];
	}
	public function get_option( $k, $default = null ) { return isset( $this->settings[ $k ] ) ? $this->settings[ $k ] : $default; }
	public function process_admin_options() {}
	public function is_available() { return 'yes' === $this->enabled; }
	public function get_return_url( $order = null ) { return 'http://localhost/checkout/order-received/' . ( $order ? $order->id : '' ); }
}

class Fake_Order {
	public $id; public $customer; public $total; public $meta = array(); public $status = 'pending'; public $notes = array();
	public function __construct( $id, $customer, $total ) { $this->id = $id; $this->customer = $customer; $this->total = $total; }
	public function get_customer_id() { return $this->customer; }
	public function get_total() { return $this->total; }
	public function update_meta_data( $k, $v ) { $this->meta[ $k ] = $v; }
	public function get_meta( $k ) { return isset( $this->meta[ $k ] ) ? $this->meta[ $k ] : ''; }
	public function save() {}
	public function update_status( $s, $note = '' ) { $this->status = $s; $this->notes[] = $note; }
}

class Fake_Cart {
	public $total = 0; public $emptied = false;
	public function get_total( $ctx = 'view' ) { return $this->total; }
	public function empty_cart() { $this->emptied = true; }
}

$GLOBALS['fake_orders'] = array();
$GLOBALS['fake_notices'] = array();
class Fake_Mailer {
	public function wrap_message( $heading, $html ) { return '<h1>' . $heading . '</h1>' . $html; }
	public function send( $to, $subject, $message ) { return wp_mail( $to, $subject, $message, array( 'Content-Type: text/html' ) ); }
}
class Fake_WC {
	public $cart;
	public function __construct() { $this->cart = new Fake_Cart(); }
	public function mailer() { return new Fake_Mailer(); }
}
class WooCommerce {}
$GLOBALS['fake_wc'] = new Fake_WC();
function WC() { return $GLOBALS['fake_wc']; }
function wc_get_order( $id ) { return isset( $GLOBALS['fake_orders'][ $id ] ) ? $GLOBALS['fake_orders'][ $id ] : false; }
function wc_add_notice( $m, $t = 'success' ) { $GLOBALS['fake_notices'][] = $m; }
function wc_price( $v ) { return '$' . number_format( (float) $v, 2 ); }
function woocommerce_form_field( $key, $args, $value = '' ) { echo '<input name="' . esc_attr( $key ) . '"' . ( ! empty( $args['required'] ) ? ' required' : '' ) . '>'; }
function wc_get_price_decimals() { return 2; }

function wc_get_page_permalink( $page ) { return home_url( '/my-account/' ); }
function wc_get_endpoint_url( $endpoint, $value = '', $permalink = '' ) { return trailingslashit( $permalink ) . $endpoint . '/' . ( $value ? trailingslashit( $value ) : '' ); }
function wc_date_format() { return 'j M Y'; }
$GLOBALS['fake_products'] = array();
function wc_get_product_id_by_sku( $sku ) { foreach ( $GLOBALS['fake_products'] as $id => $p ) { if ( $p->get_sku() === $sku ) { return $id; } } return 0; }
function wc_get_product( $id ) { return isset( $GLOBALS['fake_products'][ $id ] ) ? $GLOBALS['fake_products'][ $id ] : null; }
class Fake_Product {
	public $meta; public $sku; public $name;
	public function __construct( $sku, $name, $meta = array() ) { $this->sku = $sku; $this->name = $name; $this->meta = $meta; }
	public function get_meta( $k ) { return isset( $this->meta[ $k ] ) ? $this->meta[ $k ] : ''; }
	public function get_sku() { return $this->sku; }
	public function get_name() { return $this->name; }
}
