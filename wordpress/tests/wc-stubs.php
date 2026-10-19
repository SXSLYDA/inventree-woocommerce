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
$GLOBALS['fake_wc'] = (object) array( 'cart' => new Fake_Cart() );
function WC() { return $GLOBALS['fake_wc']; }
function wc_get_order( $id ) { return isset( $GLOBALS['fake_orders'][ $id ] ) ? $GLOBALS['fake_orders'][ $id ] : false; }
function wc_add_notice( $m, $t = 'success' ) { $GLOBALS['fake_notices'][] = $m; }
function wc_price( $v ) { return '$' . number_format( (float) $v, 2 ); }
function woocommerce_form_field( $key, $args, $value = '' ) { echo '<input name="' . esc_attr( $key ) . '"' . ( ! empty( $args['required'] ) ? ' required' : '' ) . '>'; }
function wc_get_price_decimals() { return 2; }
